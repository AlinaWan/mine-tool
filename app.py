import cv2
import numpy as np
from PIL import ImageGrab, Image, ImageTk
from pynput.mouse import Controller as MouseController, Button
from pynput import keyboard
import time
import sys
import tkinter as tk
import threading
import queue
import configparser # Import configparser

# --- Configuration ---
# Default values for configuration. These will be used if config.ini is not found
# or if specific values are missing.
DEFAULT_ROI_X1, DEFAULT_ROI_Y1, DEFAULT_ROI_X2, DEFAULT_ROI_Y2 = 960, 437, 1080, 557
DEFAULT_HEX_GREY = "#485163"
DEFAULT_HEX_WHITE = "#cecece"
DEFAULT_COLOR_TOLERANCE = 20
DEFAULT_MIDDLE_THRESHOLD = 15
DEFAULT_CLICK_COOLDOWN_DURATION = 0.5

# --- Config File Handling ---
CONFIG_FILE = 'config.ini'

def load_config():
    """
    Loads configuration from config.ini. If the file doesn't exist or is invalid,
    it creates a default config.ini.
    """
    config = configparser.ConfigParser()

    if not config.read(CONFIG_FILE):
        print(f"'{CONFIG_FILE}' not found or could not be read. Creating with default values.")
        config['Detection'] = {
            'ROI_X1': str(DEFAULT_ROI_X1),
            'ROI_Y1': str(DEFAULT_ROI_Y1),
            'ROI_X2': str(DEFAULT_ROI_X2),
            'ROI_Y2': str(DEFAULT_ROI_Y2),
            'HEX_GREY': DEFAULT_HEX_GREY,
            'HEX_WHITE': DEFAULT_HEX_WHITE,
            'COLOR_TOLERANCE': str(DEFAULT_COLOR_TOLERANCE),
            'MIDDLE_THRESHOLD': str(DEFAULT_MIDDLE_THRESHOLD)
        }
        config['Automation'] = {
            'CLICK_COOLDOWN_DURATION': str(DEFAULT_CLICK_COOLDOWN_DURATION)
        }
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
        print(f"Default '{CONFIG_FILE}' created. Please review and adjust values if needed.")
        # Reload the config after creating it
        config.read(CONFIG_FILE)

    # Read values, converting to appropriate types
    roi_x1 = config.getint('Detection', 'ROI_X1', fallback=DEFAULT_ROI_X1)
    roi_y1 = config.getint('Detection', 'ROI_Y1', fallback=DEFAULT_ROI_Y1)
    roi_x2 = config.getint('Detection', 'ROI_X2', fallback=DEFAULT_ROI_X2)
    roi_y2 = config.getint('Detection', 'ROI_Y2', fallback=DEFAULT_ROI_Y2)
    
    hex_grey = config.get('Detection', 'HEX_GREY', fallback=DEFAULT_HEX_GREY)
    hex_white = config.get('Detection', 'HEX_WHITE', fallback=DEFAULT_HEX_WHITE)
    
    color_tolerance = config.getint('Detection', 'COLOR_TOLERANCE', fallback=DEFAULT_COLOR_TOLERANCE)
    middle_threshold = config.getint('Detection', 'MIDDLE_THRESHOLD', fallback=DEFAULT_MIDDLE_THRESHOLD)
    
    click_cooldown_duration = config.getfloat('Automation', 'CLICK_COOLDOWN_DURATION', fallback=DEFAULT_CLICK_COOLDOWN_DURATION)

    return {
        'ROI_X1': roi_x1, 'ROI_Y1': roi_y1, 'ROI_X2': roi_x2, 'ROI_Y2': roi_y2,
        'HEX_GREY': hex_grey, 'HEX_WHITE': hex_white,
        'COLOR_TOLERANCE': color_tolerance, 'MIDDLE_THRESHOLD': middle_threshold,
        'CLICK_COOLDOWN_DURATION': click_cooldown_duration
    }

# Load configuration at script start
settings = load_config()

# Assign loaded settings to variables
ROI_X1, ROI_Y1, ROI_X2, ROI_Y2 = settings['ROI_X1'], settings['ROI_Y1'], settings['ROI_X2'], settings['ROI_Y2']
HEX_GREY = settings['HEX_GREY']
HEX_WHITE = settings['HEX_WHITE']
COLOR_TOLERANCE = settings['COLOR_TOLERANCE']
MIDDLE_THRESHOLD = settings['MIDDLE_THRESHOLD']
CLICK_COOLDOWN_DURATION = settings['CLICK_COOLDOWN_DURATION']

# Mouse controller setup
mouse = MouseController()

# Flag to control the script loop
running = True

# Queue for passing images from processing thread to GUI thread
image_queue = queue.Queue(maxsize=1)
mask_queue_grey = queue.Queue(maxsize=1)
mask_queue_white = queue.Queue(maxsize=1)
mask_queue_bar = queue.Queue(maxsize=1)

# Variables for cooldown mechanism
cooldown_active = False
cooldown_start_time = 0

# --- Helper Functions ---

def hex_to_bgr(hex_color):
    """
    Converts a hexadecimal color string (e.g., "#RRGGBB") to an OpenCV BGR NumPy array.
    """
    hex_color = hex_color.lstrip('#')
    return np.array([int(hex_color[4:6], 16), int(hex_color[2:4], 16), int(hex_color[0:2], 16)])

def get_screenshot(x1, y1, x2, y2):
    """
    Captures a screenshot of the specified region.
    Returns a NumPy array in BGR format (suitable for OpenCV).
    """
    try:
        img = ImageGrab.grab(bbox=(x1, y1, x2, y2))
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    except Exception as e:
        return None

def find_colored_area_bgr(bgr_image, target_bgr, color_tolerance, min_area=50):
    """
    Detects a colored area within a BGR image using a color range around the target BGR.
    Finds the largest contour, and returns its centroid, contour points,
    mask, and rotated bounding box information.
    """
    lower_bound = np.array([max(0, c - color_tolerance) for c in target_bgr])
    upper_bound = np.array([min(255, c + color_tolerance) for c in target_bgr])

    mask = cv2.inRange(bgr_image, lower_bound, upper_bound)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None, None, mask, None

    largest_contour = max(contours, key=cv2.contourArea)

    if cv2.contourArea(largest_contour) < min_area:
        return None, None, mask, None

    M = cv2.moments(largest_contour)
    if M["m00"] == 0:
        return None, None, mask, None

    cX = int(M["m10"] / M["m00"])
    cY = int(M["m01"] / M["m00"])

    rect = None
    if len(largest_contour) >= 5:
        rect = cv2.minAreaRect(largest_contour)

    return (cX, cY), largest_contour, mask, rect

def detect_curved_bar(image_bgr, min_bar_area=500):
    """
    Attempts to detect the general area of the curved bar.
    This is a simplification: it looks for the largest dark region.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    _, thresh = cv2.threshold(gray, 50, 255, cv2.THRESH_BINARY_INV)

    kernel = np.ones((5,5),np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None, thresh

    candidate_bar_contour = None
    max_area = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > min_bar_area:
            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = float(w)/h
            if (0.3 < aspect_ratio < 3.0 and area / (w*h) > 0.4) or (area > max_area):
                if area > max_area:
                    max_area = area
                    candidate_bar_contour = contour
    
    if candidate_bar_contour is None:
        return None, thresh

    return candidate_bar_contour, thresh

def on_press(key):
    """Callback for keyboard listener."""
    global running
    try:
        if key == keyboard.Key.esc:
            print("Escape pressed, stopping script.")
            running = False
            return False
    except AttributeError:
        pass

# Start keyboard listener in a non-blocking way
listener = keyboard.Listener(on_press=on_press)
listener.start()

# Convert hex colors to BGR using loaded settings
TARGET_GREY_BGR = hex_to_bgr(HEX_GREY)
TARGET_WHITE_BGR = hex_to_bgr(HEX_WHITE)

# --- Main Processing Loop (runs in a separate thread) ---
def processing_loop():
    global running, cooldown_active, cooldown_start_time
    print("Starting detection script. Press 'Esc' to stop.")
    print(f"Monitoring region: ({ROI_X1},{ROI_Y1}) to ({ROI_X2},{ROI_Y2})")
    print(f"Target Grey BGR: {TARGET_GREY_BGR}, Target White BGR: {TARGET_WHITE_BGR}")

    frame_count = 0
    while running:
        try:
            frame_count += 1
            
            # Check if cooldown is active
            if cooldown_active:
                if time.time() - cooldown_start_time >= CLICK_COOLDOWN_DURATION:
                    cooldown_active = False # Cooldown finished

            screenshot_bgr = get_screenshot(ROI_X1, ROI_Y1, ROI_X2, ROI_Y2)
            if screenshot_bgr is None:
                time.sleep(0.05)
                continue

            display_image_bgr = screenshot_bgr.copy()

            # 2. Detect White Area using BGR
            white_center, white_contour, white_mask, _ = find_colored_area_bgr(
                screenshot_bgr, TARGET_WHITE_BGR, COLOR_TOLERANCE
            )
            if white_center:
                cv2.circle(display_image_bgr, white_center, 7, (0, 255, 0), -1)
                cv2.drawContours(display_image_bgr, [white_contour], -1, (0, 255, 0), 2)
                cv2.putText(display_image_bgr, "White", (white_center[0] + 10, white_center[1] - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

            # 3. Detect Grey Line using BGR and get rotation info
            grey_center, grey_contour, grey_mask, grey_rect = find_colored_area_bgr(
                screenshot_bgr, TARGET_GREY_BGR, COLOR_TOLERANCE
            )
            if grey_center:
                cv2.circle(display_image_bgr, grey_center, 5, (255, 0, 0), -1)
                cv2.drawContours(display_image_bgr, [grey_contour], -1, (255, 0, 0), 2)
                cv2.putText(display_image_bgr, "Grey", (grey_center[0] + 10, grey_center[1] + 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)
                
                if grey_rect:
                    box = np.int32(cv2.boxPoints(grey_rect))
                    cv2.drawContours(display_image_bgr, [box], 0, (0, 255, 255), 2)
                    (x, y), (w, h), angle = grey_rect
                    radians = np.deg2rad(angle)
                    length = max(w, h) / 2
                    end_x = int(x + length * np.cos(radians))
                    end_y = int(y + length * np.sin(radians))
                    start_x = int(x - length * np.cos(radians))
                    start_y = int(y - length * np.sin(radians))
                    cv2.line(display_image_bgr, (start_x, start_y), (end_x, end_y), (255, 0, 255), 2)

            # 4. Detect Curved Bar (for visualization)
            bar_contour, bar_mask = detect_curved_bar(screenshot_bgr)
            if bar_contour is not None:
                cv2.drawContours(display_image_bgr, [bar_contour], -1, (0, 0, 255), 2)
                if bar_contour.shape[0] > 0:
                    cv2.putText(display_image_bgr, "Bar", (bar_contour[0][0][0] + 10, bar_contour[0][0][1] - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

            # 5. Logic for mouse release
            if grey_center and white_center:
                distance = np.sqrt((grey_center[0] - white_center[0])**2 + (grey_center[1] - white_center[1])**2)
                cv2.putText(display_image_bgr, f"Dist: {distance:.1f}", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

                # Only release mouse if not currently in cooldown
                if distance < MIDDLE_THRESHOLD and not cooldown_active:
                    print(f"[{frame_count}] Grey line is in the middle! Releasing mouse.")
                    mouse.release(Button.left)
                    cooldown_active = True
                    cooldown_start_time = time.time() # Start cooldown timer

            # Put images into queues for GUI thread
            try:
                image_queue.put_nowait(display_image_bgr)
                mask_queue_grey.put_nowait(grey_mask)
                mask_queue_white.put_nowait(white_mask)
                mask_queue_bar.put_nowait(bar_mask)
            except queue.Full:
                pass # Skip frame if queue is full (GUI is slow)

            time.sleep(0.01)

        except Exception as e:
            print(f"Exception in processing_loop: {e}", file=sys.stderr)
            time.sleep(1)

    print("Processing thread stopped.")


# --- Tkinter Setup for Always-on-Top Display ---
root = tk.Tk()
root.title("Detected Elements (Live)")
root.attributes("-topmost", True)
main_window_width = ROI_X2 - ROI_X1
main_window_height = ROI_Y2 - ROI_Y1
root.geometry(f"{main_window_width}x{main_window_height}")
root.resizable(False, False)

label_main = tk.Label(root)
label_main.pack()

# --- Tkinter Toplevel windows for masks ---
grey_mask_window = tk.Toplevel(root)
grey_mask_window.title("Grey Line Mask")
grey_mask_window.attributes("-topmost", True)
label_grey_mask = tk.Label(grey_mask_window)
label_grey_mask.pack()

white_mask_window = tk.Toplevel(root)
white_mask_window.title("White Area Mask")
white_mask_window.attributes("-topmost", True)
label_white_mask = tk.Label(white_mask_window)
label_white_mask.pack()

bar_mask_window = tk.Toplevel(root)
bar_mask_window.title("Curved Bar Mask (Dark Area)")
bar_mask_window.attributes("-topmost", True)
label_bar_mask = tk.Label(bar_mask_window)
label_bar_mask.pack()


def update_tkinter_image(label, cv_image, tk_window):
    """Helper to convert OpenCV image to PhotoImage and update label."""
    image_rgb = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(image_rgb)
    
    target_width = ROI_X2 - ROI_X1
    target_height = ROI_Y2 - ROI_Y1
    
    if pil_image.width != target_width or pil_image.height != target_height:
        pil_image = pil_image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    
    new_tk_image = ImageTk.PhotoImage(image=pil_image)
    label.config(image=new_tk_image)
    label.image = new_tk_image
    tk_window.lift()

def update_gui_from_queue():
    """Fetches images from queues and updates Tkinter labels."""
    try:
        display_image_bgr = image_queue.get_nowait()
        update_tkinter_image(label_main, display_image_bgr, root)
    except queue.Empty:
        pass

    try:
        grey_mask = mask_queue_grey.get_nowait()
        update_tkinter_image(label_grey_mask, cv2.cvtColor(grey_mask, cv2.COLOR_GRAY2BGR), grey_mask_window)
    except queue.Empty:
        pass

    try:
        white_mask = mask_queue_white.get_nowait()
        update_tkinter_image(label_white_mask, cv2.cvtColor(white_mask, cv2.COLOR_GRAY2BGR), white_mask_window)
    except queue.Empty:
        pass

    try:
        bar_mask = mask_queue_bar.get_nowait()
        update_tkinter_image(label_bar_mask, cv2.cvtColor(bar_mask, cv2.COLOR_GRAY2BGR), bar_mask_window)
    except queue.Empty:
        pass

    root.after(10, update_gui_from_queue)


# --- Initialize and Start ---
processing_thread = threading.Thread(target=processing_loop)
processing_thread.daemon = True
processing_thread.start()

def set_initial_window_positions():
    root.update_idletasks()
    root_x = root.winfo_x()
    root_y = root.winfo_y()

    window_width = ROI_X2 - ROI_X1
    window_height = ROI_Y2 - ROI_Y1
    
    grey_mask_window.geometry(f"{window_width}x{window_height}+{root_x + window_width + 10}+{root_y}")
    white_mask_window.geometry(f"{window_width}x{window_height}+{root_x + 2*window_width + 20}+{root_y}")
    bar_mask_window.geometry(f"{window_width}x{window_height}+{root_x + 3*window_width + 30}+{root_y}")
    
    grey_mask_window.resizable(False, False)
    white_mask_window.resizable(False, False)
    bar_mask_window.resizable(False, False)

root.after(100, set_initial_window_positions)

root.after(10, update_gui_from_queue)

try:
    root.mainloop()
except Exception as e:
    print(f"Tkinter mainloop error: {e}", file=sys.stderr)
finally:
    running = False
    listener.stop()

    if processing_thread.is_alive():
        processing_thread.join(timeout=1.0)

    if 'grey_mask_window' in locals() and grey_mask_window.winfo_exists():
        grey_mask_window.destroy()
    if 'white_mask_window' in locals() and white_mask_window.winfo_exists():
        white_mask_window.destroy()
    if 'bar_mask_window' in locals() and bar_mask_window.winfo_exists():
        bar_mask_window.destroy()
    if 'root' in locals() and root.winfo_exists():
        root.destroy()
    
    print("Script finished.")
