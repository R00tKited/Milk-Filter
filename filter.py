#!/usr/bin/env python3
"""Milk filter: turn any image into something you would find in Milk inside/outside a bag of milk.

Run it without arguments to open the GUI, or with arguments to use it from the command line:
    python filter.py -f image.png -a -p -c 30 -o saved.png
"""
import argparse
import io
import os
import queue
import random
import sys
import threading

from PIL import Image, ImageMath, ImageOps

try:
    import tkinter as tk
    from tkinter import filedialog as fd
    from tkinter import messagebox, ttk

    from PIL import ImageTk
except ImportError:  # Tk is only needed for the GUI, the command line works without it
    tk = None


# Colors of each game: black, dark shade and bright shade.
PALETTES = {
    1: [(0, 0, 0), (102, 0, 31), (137, 0, 146)],
    2: [(0, 0, 0), (92, 36, 60), (203, 43, 43)],
}
# Brightness where the dark shade stops mixing with black, and where the bright shade starts.
MID_THRESHOLDS = {1: (120, 200), 2: (90, 150)}
# With the pointillism effect, a pixel keeps its color with this chance and takes the neighbouring one otherwise.
POINTILLISM_CHANCE = 0.7


def _color_tables(milk_type):
    """Palette index for every possible R+G+B sum (0..765).

    Returns two tables: the color a pixel normally gets, and the one it gets when the
    pointillism effect swaps it (they only differ in the mixed brightness ranges).
    """
    mid1, mid2 = MID_THRESHOLDS[milk_type]
    usual, swapped = [], []
    for total in range(766):
        brightness = total / 3
        if brightness <= 25:
            pair = (0, 0)
        elif brightness <= 70:
            pair = (0, 1)
        elif brightness < mid1:
            pair = (1, 0)
        elif brightness < mid2:
            pair = (1, 1)
        elif brightness < 230:
            pair = (2, 1)
        else:
            pair = (2, 2)
        usual.append(pair[0])
        swapped.append(pair[1])
    return usual, swapped


def _band_sum(img):
    """R+G+B of every pixel of an RGB image, as a 32-bit integer image."""
    r, g, b = img.split()
    if hasattr(ImageMath, "lambda_eval"):  # Pillow >= 10.3
        return ImageMath.lambda_eval(lambda args: args["r"] + args["g"] + args["b"], r=r, g=g, b=b)
    return ImageMath.eval("r + g + b", r=r, g=g, b=b)


def _to_indices(total, table):
    """Turn the R+G+B image into palette indices with a lookup table."""
    return total.point(table + [0] * (65536 - len(table)), "L")


def _alpha(img):
    """Transparency of img, or None when it is fully opaque."""
    if img.mode not in ("RGBA", "LA", "PA") and "transparency" not in img.info:
        return None
    alpha = img.convert("RGBA").getchannel("A")
    return None if alpha.getextrema() == (255, 255) else alpha


def upright(img):
    """Copy of img turned the right way up: phones save photos rotated, with the rotation in the EXIF data."""
    try:
        return ImageOps.exif_transpose(img)
    except Exception:  # broken EXIF data, keep the image as it is stored
        return img.copy()


def apply_milk_filter(img, milk_type=1, pointillism=False, compression=0, seed=None):
    """Return a copy of img in the colors of Milk1 (milk_type=1) or Milk2 (milk_type=2).

    compression (0-100) lowers the JPEG quality first for a more pixelated look,
    seed makes the pointillism effect repeatable. Transparency of img is kept.
    """
    img = upright(img)
    alpha = _alpha(img)
    img = img.convert("RGB")

    if compression > 0:
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=max(1, 100 - compression))
        buffer.seek(0)
        img = Image.open(buffer).convert("RGB")

    # Each pixel gets one of the three colors depending on its brightness, (R+G+B)/3.
    # The sum R+G+B carries the same information in whole numbers, so lookup tables can do the work.
    total = _band_sum(img)
    usual, swapped = _color_tables(milk_type)
    indices = _to_indices(total, usual)
    if pointillism:
        noise = Image.frombytes("L", img.size, random.Random(seed).randbytes(img.width * img.height))
        limit = round(POINTILLISM_CHANCE * 256)
        keep = noise.point(lambda value: 255 if value < limit else 0)
        indices = Image.composite(indices, _to_indices(total, swapped), keep)

    indices.putpalette([channel for color in PALETTES[milk_type] for channel in color])
    result = indices.convert("RGB")
    if alpha is not None:
        result.putalpha(alpha)
    return result


def save_image(img, path):
    """Save img in the format given by the file extension (.png, .jpg, ...)."""
    extension = os.path.splitext(path)[1].lower()
    Image.init()  # load every format plugin, older Pillow doesn't do it in registered_extensions()
    image_format = Image.registered_extensions().get(extension)
    if image_format not in Image.SAVE:
        raise ValueError(f"Can't save images as {extension or 'a file without extension'}, use .png or .jpg")
    if image_format == "JPEG" and img.mode == "RGBA":
        img = img.convert("RGB")  # JPEG can't store transparency
    img.save(path, format=image_format)


def run_cli(argv):
    parser = argparse.ArgumentParser(description="Milk image filter. Run without arguments to open the GUI.")
    parser.add_argument("-f", "--file", help="Specify input image.", required=True)
    parser.add_argument("-o", "--out", help="Specify out path.", required=True)
    parser.add_argument("-a", "--alt", help="Alternative Milk effect (the effect from the second game).",
                        action="store_true")
    parser.add_argument("-p", "--pointism", help="Pointillism effect.", action="store_true")
    parser.add_argument("-c", "--comp", help="Compression. from 0 to 100. Defaults to 0.", default=0, type=int)
    args = parser.parse_args(argv)

    try:
        with Image.open(args.file) as img:
            result = apply_milk_filter(img, milk_type=2 if args.alt else 1, pointillism=args.pointism,
                                       compression=args.comp)
        save_image(result, args.out)
    except (OSError, ValueError) as error:
        sys.exit(f"Error: {error}")


def resource_path(relative_path):
    """Absolute path to a file next to this script, or inside the EXE made with PyInstaller."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def fit(img, size):
    """Scale img to fit in size, keeping its proportions."""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    return ImageOps.contain(img, size, Image.Resampling.LANCZOS)


def _describe(error):
    return str(error) or type(error).__name__


class MilkFilterApp:
    def __init__(self, window):
        self.window = window
        self.source = None  # the opened image
        self.result = None  # the filtered image
        self.job = 0  # number of the latest filter run, results of older runs are dropped
        self.results = queue.Queue()  # filter results coming from the background thread
        self.viewer = None

        window.title("Milk filter!")
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        window.geometry(f"{screen_width}x{screen_height}")
        # state zoomed doesnt work on x11 system
        if os.name == "nt":
            window.state("zoomed")

        # A portrait screen (like a phone with Pydroid 3) gets short texts and the images under each other.
        self.compact = screen_height > screen_width
        if self.compact:
            self.preview_size = (int(screen_width / 1.1), int(screen_height / 2.6))
        else:
            self.preview_size = (int(screen_width / 2.2), int(screen_height / 2.2))
        self.viewer_size = (int(screen_width / 1.5), int(screen_height / 1.5))

        try:
            self.icon = ImageTk.PhotoImage(file=resource_path("icon.ico"))
            window.iconphoto(True, self.icon)
        except (OSError, tk.TclError):  # the icon is optional, e.g. when only filter.py was downloaded
            self.icon = None

        self._build_scroll_area()
        self._build_widgets()
        self._check_results()

    def _build_scroll_area(self):
        main_frame = tk.Frame(self.window)
        main_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(main_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.content = tk.Frame(self.canvas)
        self.content_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.bind("<Configure>", self._update_scroll_area)
        self.content.bind("<Configure>", self._update_scroll_area)
        # The wheel is <MouseWheel> on Windows and macOS (and on X11 since Tk 8.7), <Button-4/5> on X11 before.
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.window.bind_all(sequence, self._on_mouse_wheel)

    def _update_scroll_area(self, event=None):
        """Stretch the content to the canvas width, center it when it fits and let it scroll when it doesn't."""
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        content_height = self.content.winfo_reqheight()
        self.canvas.itemconfigure(self.content_window, width=width)
        self.canvas.coords(self.content_window, 0, max(0, (height - content_height) // 2))
        self.canvas.configure(scrollregion=(0, 0, width, max(height, content_height)))

    def _on_mouse_wheel(self, event):
        if event.num == 4:
            units = -1
        elif event.num == 5:
            units = 1
        elif abs(event.delta) >= 120:  # Windows and newer Tk: 120 per wheel step
            units = int(-event.delta / 120)
        elif event.delta:  # macOS and touchpads send smaller steps
            units = -1 if event.delta > 0 else 1
        else:
            return
        self.canvas.yview_scroll(units, "units")

    def _build_widgets(self):
        top = ttk.Frame(self.content)
        images = ttk.Frame(self.content)
        self.options = ttk.Frame(self.content)  # shown once an image is opened
        top.pack(pady=5)
        images.pack(pady=5)

        ttk.Button(top, text="Open a File", command=self.select_file).pack()

        width, height = self.preview_size
        self.placeholder = ImageTk.PhotoImage(Image.new("RGB", self.preview_size, (203, 203, 203)))
        side = tk.TOP if self.compact else tk.LEFT
        self.original_label = tk.Label(images, image=self.placeholder, width=width, height=height)
        self.original_label.pack(side=side)
        self.result_label = tk.Label(images, image=self.placeholder, width=width, height=height, compound="center")
        self.result_label.pack(side=side)

        self.compression = tk.IntVar(value=0)
        self.compression_level = tk.IntVar(value=0)
        self.pointillism = tk.IntVar(value=0)
        self.milk = tk.IntVar(value=1)
        if self.compact:
            compression_text = "Compression?"
            level_text = "Level of compression (0 best, 100 worst): "
            pointillism_text = "Pointillism effect?"
        else:
            compression_text = "Check this box if you want compression on the image or not."
            level_text = "Level of compression (from 0 best quality to 100 worst quality): "
            pointillism_text = "Check this box if you want the pointillism effect on the image or not."

        tk.Checkbutton(self.options, text=compression_text, variable=self.compression,
                       command=self._toggle_compression).grid(row=0)
        self.level_label = tk.Label(self.options, text="0")
        self.compression_widgets = [
            tk.Label(self.options, text=level_text),
            ttk.Scale(self.options, variable=self.compression_level, from_=0, to=100, command=self._on_level_change),
            self.level_label,
        ]
        for row, widget in enumerate(self.compression_widgets, start=1):
            widget.grid(row=row)
            widget.grid_remove()
        tk.Checkbutton(self.options, text=pointillism_text, variable=self.pointillism).grid(row=4)
        tk.Radiobutton(self.options, text="Milk1 effect", variable=self.milk, value=1).grid(row=5)
        tk.Radiobutton(self.options, text="Milk2 effect", variable=self.milk, value=2).grid(row=6)
        self.apply_button = ttk.Button(self.options, text="Apply filter", command=self.apply_filter)
        self.apply_button.grid(row=7)
        self.progress = ttk.Progressbar(self.options, orient="horizontal", length=300, mode="indeterminate")
        self.progress.grid(row=8, pady=5)
        self.progress.grid_remove()
        self.save_button = ttk.Button(self.options, text="Save image", command=self.save)
        self.save_button.grid(row=9, pady=5)
        self.save_button.grid_remove()

    def _toggle_compression(self):
        for widget in self.compression_widgets:
            if self.compression.get() == 1:
                widget.grid()
            else:
                widget.grid_remove()

    def _on_level_change(self, value):
        self.level_label.configure(text=str(int(float(value))))

    def _show(self, label, img, size=None):
        photo = ImageTk.PhotoImage(fit(img, size or self.preview_size))
        label.configure(image=photo, text="")
        label.image = photo  # keep a reference, otherwise Tk shows nothing

    def _clear_result(self, text=""):
        self.result = None
        self.result_label.configure(image=self.placeholder, text=text)
        self.save_button.grid_remove()

    def _set_busy(self, busy):
        if busy:
            self.apply_button.state(["disabled"])
            self.progress.grid()
            self.progress.start(10)
        else:
            self.progress.stop()
            self.progress.grid_remove()
            self.apply_button.state(["!disabled"])

    def select_file(self):
        filetypes = [("Image files (.png,.jpg,.jpeg)", "*.png *.jpg *.jpeg")]
        filename = fd.askopenfilename(title="Open a file", initialdir=".", filetypes=filetypes)
        if not filename:
            return
        try:
            with Image.open(filename) as img:
                source = upright(img)
        except Exception as error:  # not an image, no permission, ...
            messagebox.showerror("Could not open the image", _describe(error))
            return

        self.source = source
        self.job += 1  # a filter run for the previous image is not wanted anymore
        self._set_busy(False)
        self._show(self.original_label, source)
        self._clear_result()
        self.options.pack(pady=5)

    def apply_filter(self):
        settings = {
            "milk_type": self.milk.get(),
            "pointillism": self.pointillism.get() == 1,
            "compression": self.compression_level.get() if self.compression.get() == 1 else 0,
        }
        self.job += 1
        self._set_busy(True)
        self._clear_result("Processing...")
        # The thread gets plain values and its own copy of the image: Tk may only be used from this thread.
        threading.Thread(target=self._run_filter, args=(self.job, self.source.copy(), settings), daemon=True).start()

    def _run_filter(self, job, source, settings):
        """Runs in a background thread, the result goes back through the queue."""
        try:
            self.results.put((job, apply_milk_filter(source, **settings), None))
        except Exception as error:
            self.results.put((job, None, error))

    def _check_results(self):
        """Pick up finished filter runs, every 100 ms on the Tk thread."""
        try:
            while True:
                job, result, error = self.results.get_nowait()
                if job == self.job:
                    self._finish(result, error)
        except queue.Empty:
            pass
        self.window.after(100, self._check_results)

    def _finish(self, result, error):
        self._set_busy(False)
        if error is not None:
            self._clear_result()
            messagebox.showerror("Could not apply the filter", _describe(error))
            return
        self.result = result
        self._show(self.result_label, result)
        self.save_button.grid()
        self._show_viewer(result)

    def _show_viewer(self, result):
        if self.viewer is None or not self.viewer.winfo_exists():
            self.viewer = tk.Toplevel(self.window)
            self.viewer.title("Image Viewer")
            self.viewer_label = tk.Label(self.viewer)
            self.viewer_label.pack(fill=tk.BOTH, expand=True)
            self.viewer_button = ttk.Button(self.viewer, text="See file in image viewer")
            self.viewer_button.pack(expand=True)
        self._show(self.viewer_label, result, self.viewer_size)
        self.viewer_button.configure(command=result.show)
        self.viewer.deiconify()
        self.viewer.lift()

    def save(self):
        filetypes = [(".png", ".png"), (".jpg", ".jpg"), (".jpeg", ".jpeg")]
        filename = fd.asksaveasfilename(defaultextension=".png", filetypes=filetypes)
        if not filename:
            return
        try:
            save_image(self.result, filename)
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not save the image", _describe(error))


def main():
    if len(sys.argv) > 1:
        run_cli(sys.argv[1:])
    elif tk is None:
        sys.exit("The GUI needs tkinter. Install it, or use the command line: python filter.py --help")
    else:
        window = tk.Tk()
        MilkFilterApp(window)
        window.mainloop()


if __name__ == "__main__":
    main()
