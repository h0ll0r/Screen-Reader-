import tkinter as tk
import threading
import queue
import time
import sys
import os
import subprocess

# ─── Настройки ───────────────────────────────────────────────
HOTKEY         = "f9"
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
OCR_LANG       = "rus+eng"
TTS_RATE       = 175
# ──────────────────────────────────────────────────────────────

def pip_install(pkg):
    subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q",
                    "--user"], check=False)

try:
    from PIL import Image, ImageTk
except ImportError:
    pip_install("Pillow")
    from PIL import Image, ImageTk

try:
    import pytesseract
except ImportError:
    pip_install("pytesseract")
    import pytesseract

try:
    import pyttsx3
except ImportError:
    pip_install("pyttsx3")
    import pyttsx3

try:
    import keyboard
except ImportError:
    pip_install("keyboard")
    import keyboard

try:
    import pygetwindow as gw
except ImportError:
    pip_install("pygetwindow")
    import pygetwindow as gw

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


# ─── TTS: один поток + очередь ────────────────────────────────
tts_queue = queue.Queue()

def tts_worker():
    engine = pyttsx3.init()
    engine.setProperty("rate", TTS_RATE)
    for v in engine.getProperty("voices"):
        if "russian" in v.name.lower() or "ru" in v.id.lower():
            engine.setProperty("voice", v.id)
            break
    while True:
        text = tts_queue.get()
        if text is None:
            break
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"[TTS error] {e}")
            # Пересоздаём движок если сломался
            try:
                engine.stop()
            except Exception:
                pass
            engine = pyttsx3.init()
            engine.setProperty("rate", TTS_RATE)
        tts_queue.task_done()

def speak(text):
    text = text.strip()
    if not text:
        print("[!] Текст не распознан.")
        return
    print(f"[TTS] Озвучиваю: {text[:100]}{'...' if len(text)>100 else ''}")
    tts_queue.put(text)


# ─── Скриншот через WinAPI (BitBlt) ──────────────────────────
def screenshot_winapi() -> Image.Image:
    """
    Захват через PrintWindow + BitBlt.
    Работает с DirectX/OpenGL играми, в т.ч. fullscreen.
    Не требует opencv.
    """
    import ctypes
    from ctypes import wintypes

    user32   = ctypes.windll.user32
    gdi32    = ctypes.windll.gdi32

    # Размер всего виртуального экрана (мультимонитор)
    SM_XVIRTUALSCREEN  = 76
    SM_YVIRTUALSCREEN  = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    x  = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y  = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w  = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h  = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

    hdesktop = user32.GetDesktopWindow()
    hdc_src  = user32.GetWindowDC(hdesktop)
    hdc_dst  = gdi32.CreateCompatibleDC(hdc_src)
    hbmp     = gdi32.CreateCompatibleBitmap(hdc_src, w, h)
    gdi32.SelectObject(hdc_dst, hbmp)

    # SRCCOPY = 0x00CC0020
    gdi32.BitBlt(hdc_dst, 0, 0, w, h, hdc_src, x, y, 0x00CC0020)

    # Читаем пиксели
    import ctypes
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize",          ctypes.c_uint32),
            ("biWidth",         ctypes.c_int32),
            ("biHeight",        ctypes.c_int32),
            ("biPlanes",        ctypes.c_uint16),
            ("biBitCount",      ctypes.c_uint16),
            ("biCompression",   ctypes.c_uint32),
            ("biSizeImage",     ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32),
            ("biClrUsed",       ctypes.c_uint32),
            ("biClrImportant",  ctypes.c_uint32),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize      = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth     = w
    bmi.biHeight    = -h   # отрицательный = top-down
    bmi.biPlanes    = 1
    bmi.biBitCount  = 32
    bmi.biCompression = 0  # BI_RGB

    buf = (ctypes.c_char * (w * h * 4))()
    gdi32.GetDIBits(hdc_dst, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
    img = img.convert("RGB")

    # Освобождаем ресурсы
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_dst)
    user32.ReleaseDC(hdesktop, hdc_src)

    return img


def take_screenshot() -> Image.Image:
    # Сначала пробуем WinAPI BitBlt
    try:
        img = screenshot_winapi()
        extrema = img.convert("L").getextrema()
        if extrema != (0, 0):
            print("[OK] WinAPI захват успешен.")
            return img
        print("[!] WinAPI дал чёрный экран, пробую PrintWindow...")
    except Exception as e:
        print(f"[!] WinAPI ошибка: {e}")

    # Fallback: PrintWindow для конкретного окна игры
    try:
        return screenshot_printwindow()
    except Exception as e:
        print(f"[!] PrintWindow ошибка: {e}")
        raise RuntimeError("Не удалось сделать скриншот ни одним методом.")


def screenshot_printwindow() -> Image.Image:
    """
    PrintWindow с флагом PW_RENDERFULLCONTENT (0x2) —
    специально для игр на DirectX.
    Находит окно на переднем плане.
    """
    import ctypes
    user32 = ctypes.windll.user32
    gdi32  = ctypes.windll.gdi32

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("Нет активного окна")

    rc = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    w = rc.right  - rc.left
    h = rc.bottom - rc.top
    if w <= 0 or h <= 0:
        raise RuntimeError("Некорректный размер окна")

    hdc_src = user32.GetWindowDC(hwnd)
    hdc_dst = gdi32.CreateCompatibleDC(hdc_src)
    hbmp    = gdi32.CreateCompatibleBitmap(hdc_src, w, h)
    gdi32.SelectObject(hdc_dst, hbmp)

    # PW_RENDERFULLCONTENT = 0x00000002 (работает с DX/GL)
    user32.PrintWindow(hwnd, hdc_dst, 0x00000002)

    import ctypes as ct
    class BITMAPINFOHEADER(ct.Structure):
        _fields_ = [
            ("biSize",        ct.c_uint32), ("biWidth",   ct.c_int32),
            ("biHeight",      ct.c_int32),  ("biPlanes",  ct.c_uint16),
            ("biBitCount",    ct.c_uint16), ("biCompression", ct.c_uint32),
            ("biSizeImage",   ct.c_uint32), ("biXPelsPerMeter", ct.c_int32),
            ("biYPelsPerMeter", ct.c_int32),("biClrUsed", ct.c_uint32),
            ("biClrImportant",ct.c_uint32),
        ]
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ct.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w; bmi.biHeight = -h
    bmi.biPlanes = 1; bmi.biBitCount = 32; bmi.biCompression = 0

    buf = (ct.c_char * (w * h * 4))()
    gdi32.GetDIBits(hdc_dst, hbmp, 0, h, buf, ct.byref(bmi), 0)

    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_dst)
    user32.ReleaseDC(hwnd, hdc_src)

    return img


# ─── Окно выделения зоны ──────────────────────────────────────
class SelectionOverlay:
    def __init__(self, screenshot_img: Image.Image):
        self.screenshot = screenshot_img
        self.result = None
        self.start_x = self.start_y = 0
        self.end_x = self.end_y = 0
        self.rect_id = None

        self.root = tk.Tk()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.configure(cursor="crosshair")

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.scale_x = screenshot_img.width  / sw
        self.scale_y = screenshot_img.height / sh

        self.canvas = tk.Canvas(self.root, width=sw, height=sh,
                                highlightthickness=0, cursor="crosshair", bg="black")
        self.canvas.pack(fill="both", expand=True)

        resized = screenshot_img.resize((sw, sh), Image.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(resized)
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)
        self.canvas.create_rectangle(0, 0, sw, 28, fill="#000000")
        self.canvas.create_text(sw//2, 14, fill="#ffffff", font=("Arial", 12),
            text="Выделите зону мышью → Enter  |  Esc — отмена")

        self.canvas.bind("<ButtonPress-1>",   self._press)
        self.canvas.bind("<B1-Motion>",       self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.root.bind("<Return>", self._enter)
        self.root.bind("<Escape>", self._escape)

    def _press(self, e):
        self.start_x, self.start_y = e.x, e.y
        self.end_x,   self.end_y   = e.x, e.y
        if self.rect_id: self.canvas.delete(self.rect_id)

    def _drag(self, e):
        self.end_x, self.end_y = e.x, e.y
        if self.rect_id: self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x, self.start_y, e.x, e.y,
            outline="#ff0000", width=2, dash=(4, 2))

    def _release(self, e):
        self.end_x, self.end_y = e.x, e.y

    def _enter(self, e):
        x1 = min(self.start_x, self.end_x)
        y1 = min(self.start_y, self.end_y)
        x2 = max(self.start_x, self.end_x)
        y2 = max(self.start_y, self.end_y)
        if x2 - x1 < 5 or y2 - y1 < 5: return
        # scale обратно к реальным пикселям скриншота
        self.result = (int(x1*self.scale_x), int(y1*self.scale_y),
                       int(x2*self.scale_x), int(y2*self.scale_y))
        self.root.destroy()

    def _escape(self, e):
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.result


# ─── OCR ──────────────────────────────────────────────────────
def ocr_region(img: Image.Image, region: tuple) -> str:
    x1, y1, x2, y2 = region
    cropped = img.crop((x1, y1, x2, y2))
    w, h = cropped.size
    scale = 3 if min(w, h) < 100 else 2
    cropped = cropped.resize((w*scale, h*scale), Image.LANCZOS)
    return pytesseract.image_to_string(cropped, lang=OCR_LANG, config="--psm 6")


# ─── Основной сценарий ────────────────────────────────────────
def trigger():
    print(f"\n[{HOTKEY.upper()}] Делаю скриншот...")
    time.sleep(0.3)   # дать время отпустить клавишу и игре отрисоваться
    try:
        img = take_screenshot()
    except Exception as e:
        print(f"[!] Ошибка скриншота: {e}")
        return

    overlay = SelectionOverlay(img)
    region  = overlay.run()
    if region is None:
        print("[!] Выделение отменено.")
        return

    print(f"[OCR] Распознаю зону {region}...")
    text = ocr_region(img, region)
    if not text.strip():
        print("[!] Текст не найден.")
    else:
        speak(text)


def hotkey_listener():
    keyboard.add_hotkey(HOTKEY,
        lambda: threading.Thread(target=trigger, daemon=True).start())
    while True:
        try:
            keyboard.read_event(suppress=False)
        except Exception:
            time.sleep(0.05)


def main():
    print("=" * 50)
    print("  Screen Reader — Озвучка текста с экрана")
    print("=" * 50)
    print(f"  Горячая клавиша : {HOTKEY.upper()}")
    print(f"  Языки OCR       : {OCR_LANG}")
    print("  Методы захвата  : WinAPI BitBlt → PrintWindow")
    print("=" * 50)
    print("  Нажми F9 → выдели зону → Enter | Ctrl+C выход")
    print("=" * 50)

    threading.Thread(target=tts_worker, daemon=True).start()
    threading.Thread(target=hotkey_listener, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        tts_queue.put(None)
        print("\n[!] Выход.")
        sys.exit(0)

if __name__ == "__main__":
    main()
