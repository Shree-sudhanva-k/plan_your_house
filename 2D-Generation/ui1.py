import tkinter as tk
import math
from tkinter import Button, Text, Frame, Label, Entry, DoubleVar
from PIL import Image, ImageDraw, ImageTk
from predict import predict_prepare
from functools import partial
from prompt2json import prompt2json, updatePrompt
import google.generativeai as genai
import json

api_info = json.load(open("api_info.json"))

genai.configure(api_key=api_info["api_key"])
client = genai.GenerativeModel(api_info["model"])


class DrawingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Floor Plan Generator")

        # Start maximized / fullscreen friendly
        try:
            self.root.state('zoomed')  # Windows
        except Exception:
            try:
                self.root.attributes('-zoomed', True)  # Some Linux
            except Exception:
                self.root.attributes('-fullscreen', True)  # fallback

        # Top / bottom frames
        top_frame = Frame(root)
        top_frame.pack(side=tk.TOP, fill=tk.X)

        pil_image = Image.open("label.png")
        resized_image = pil_image.resize((400, 100), Image.Resampling.LANCZOS)
        self.label_image = ImageTk.PhotoImage(resized_image)
        label = Label(top_frame, image=self.label_image)
        label.pack(side=tk.BOTTOM)

        bottom_frame = Frame(root)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=5, padx=5)

        # Controls
        self.draw_mode_button = Button(top_frame, text="Line", command=self.toggle_draw_mode)
        self.draw_mode_button.pack(side=tk.LEFT, padx=5)

        undo_button = Button(top_frame, text="Cancel (ctrl-z)", command=self.undo)
        undo_button.pack(side=tk.LEFT, pady=5, padx=5)

        clear_button = Button(top_frame, text="Clear", command=self.clear_canvas)
        clear_button.pack(side=tk.RIGHT, pady=5, padx=5)

        self.generate_button = Button(
            top_frame, text="Generate", command=partial(self.generate_image, repredict=False)
        )
        self.generate_button.pack(side=tk.RIGHT, pady=5, padx=5)

        self.regenerate_button = Button(
            top_frame, text="Regenerate", command=partial(self.generate_image, repredict=True)
        )
        self.regenerate_button.pack(side=tk.RIGHT, pady=5, padx=5)
        self.regenerate_button.config(state=tk.DISABLED)

        self.save_button = Button(top_frame, text="Save", command=self.save_image)
        self.save_button.pack(side=tk.RIGHT, pady=5, padx=5)
        self.save_button.config(state=tk.DISABLED)

        # Scale control (pixels per meter)
        Label(top_frame, text="Scale (px per m):").pack(side=tk.LEFT, padx=(10, 2))
        self.scale_var = DoubleVar(value=20.0)
        self.scale_entry = Entry(top_frame, textvariable=self.scale_var, width=6)
        self.scale_entry.pack(side=tk.LEFT)

        self.text_input_label = Label(bottom_frame, text="Text prompt")
        self.text_input_label.pack(side=tk.LEFT, padx=5)

        self.text_input = Text(bottom_frame, width=45, height=4)
        self.text_input.place
        self.text_input.pack(side=tk.RIGHT, padx=5)

        # Canvas (resizable)
        self.canvas = tk.Canvas(root, bg="white")
        self.canvas.pack(expand=True, fill=tk.BOTH)

        # Drawing state
        self.drawing_enabled = False
        self.last_point = None
        # lines: list of tuples ((x1,y1),(x2,y2), label_id)
        self.lines = []

        # Temporary tags
        self.temp_line_tag = "temp_line"
        self.temp_text_tag = "temp_text"

        # Internal PIL image to draw into (kept in sync on resize)
        self.canvas_width = 800
        self.canvas_height = 600
        self.image = Image.new("RGB", (self.canvas_width, self.canvas_height), "white")
        self.image_draw = ImageDraw.Draw(self.image)

        # Padding / drawable region (will be updated on resize)
        self.padding = 20
        
        # Calculate SQUARE draw region centered in canvas - FIXED SIZE 400x400
        # This matches the original model training size
        self.fixed_draw_size = 400  # Fixed size to match original
        center_x = self.canvas_width // 2
        center_y = self.canvas_height // 2
        half_size = self.fixed_draw_size // 2
        
        self.draw_region = (
            center_x - half_size,
            center_y - half_size,
            center_x + half_size,
            center_y + half_size,
        )

        # Store generated prediction separately
        self.generated_prediction = None
        self.is_showing_prediction = False

        # Bindings
        self.canvas.bind("<Button-1>", self.handle_click)
        self.canvas.bind("<Motion>", self.on_mouse_move)
        self.root.bind("<Control-z>", self.undo)
        self.root.bind("<Escape>", self.exit_draw_mode)
        self.canvas.bind("<Configure>", self.on_resize)

        self.text_history = []
        self.binary_image = None
        self.mid = None

        self.trainer = predict_prepare()

        # Initial draw elements
        self.redraw_canvas()

    # ---------------------- window / canvas resizing ----------------------
    def on_resize(self, event):
        # Update stored dimensions and padding
        new_w, new_h = max(100, event.width), max(100, event.height)
        if new_w == self.canvas_width and new_h == self.canvas_height:
            return
        self.canvas_width, self.canvas_height = new_w, new_h
        
        # Keep draw region FIXED at 400x400, just re-center it
        center_x = self.canvas_width // 2
        center_y = self.canvas_height // 2
        half_size = self.fixed_draw_size // 2
        
        self.draw_region = (
            center_x - half_size,
            center_y - half_size,
            center_x + half_size,
            center_y + half_size,
        )
        
        # recreate internal image and redraw existing lines onto it
        self.image = Image.new("RGB", (self.canvas_width, self.canvas_height), "white")
        self.image_draw = ImageDraw.Draw(self.image)
        self.redraw_canvas()

    # ---------------------- drawing controls ----------------------
    def toggle_draw_mode(self):
        self.drawing_enabled = not self.drawing_enabled
        self.draw_mode_button.config(text="Exit (Esc)" if self.drawing_enabled else "Line")
        if not self.drawing_enabled:
            self.canvas.delete(self.temp_line_tag)
            self.canvas.delete(self.temp_text_tag)
            self.last_point = None

    def handle_click(self, event):
        if not self.drawing_enabled:
            return

        # Must click inside the draw region
        if not (self.draw_region[0] <= event.x <= self.draw_region[2] and self.draw_region[1] <= event.y <= self.draw_region[3]):
            # ignore clicks outside drawable region
            return

        snap_point = self.get_snap_point(event.x, event.y)
        if self.last_point:
            x1, y1 = self.last_point
            x2, y2 = snap_point
            # Draw permanent line on canvas and image
            line_id = self.canvas.create_line(x1, y1, x2, y2, fill="black", width=2)
            self.image_draw.line((x1, y1, x2, y2), fill="black", width=2)

            # Calculate and draw permanent length label
            pixel_length = math.hypot(x2 - x1, y2 - y1)
            real_length = pixel_length / float(self.scale_var.get())
            label_x = (x1 + x2) / 2
            label_y = (y1 + y2) / 2
            label_id = self.canvas.create_text(label_x, label_y - 10, text=f"{real_length:.2f} m", fill="blue", font=("Arial", 10))

            self.lines.append(((x1, y1), (x2, y2), label_id))

            # cleanup temp artifacts
            self.canvas.delete(self.temp_line_tag)
            self.canvas.delete(self.temp_text_tag)

        self.last_point = snap_point

    def on_mouse_move(self, event):
        if not self.drawing_enabled or not self.last_point:
            return

        snap_point = self.get_snap_point(event.x, event.y)
        # update preview line
        self.canvas.delete(self.temp_line_tag)
        self.canvas.create_line(self.last_point[0], self.last_point[1], snap_point[0], snap_point[1], fill="black", dash=(4, 2), tags=self.temp_line_tag)

        # update floating measurement near cursor
        self.canvas.delete(self.temp_text_tag)
        x1, y1 = self.last_point
        x2, y2 = snap_point
        pixel_length = math.hypot(x2 - x1, y2 - y1)
        # avoid division by zero
        scale = float(self.scale_var.get()) if self.scale_var.get() > 0 else 1.0
        real_length = pixel_length / scale
        text_x = min(max(10, event.x + 10), self.canvas_width - 40)
        text_y = min(max(10, event.y - 10), self.canvas_height - 10)
        self.canvas.create_text(text_x, text_y, text=f"{real_length:.2f} m", fill="blue", font=("Arial", 10), tags=self.temp_text_tag)

    def get_snap_point(self, x, y):
        # Keep points inside the drawing region
        x = min(max(x, self.draw_region[0]), self.draw_region[2])
        y = min(max(y, self.draw_region[1]), self.draw_region[3])

        # Calculate the angle for orthogonal snapping
        if self.last_point:
            dx, dy = x - self.last_point[0], y - self.last_point[1]
            if dx != 0:
                angle = abs(math.degrees(math.atan(dy / dx)))
                # Check if the angle is within 10 degrees of horizontal or vertical
                if angle < 10 or angle > 80:
                    if abs(dx) > abs(dy):
                        # horizontal snap
                        for line in self.lines:
                            if abs(x - line[0][0]) < 10:
                                return (line[0][0], self.last_point[1])
                        return (x, self.last_point[1])
                    else:
                        # vertical snap
                        for line in self.lines:
                            if abs(y - line[0][1]) < 10:
                                return (self.last_point[0], line[0][1])
                        return (self.last_point[0], y)
        # Endpoint snapping
        for line in self.lines:
            for point in (line[0], line[1]):
                if abs(x - point[0]) < 10 and abs(y - point[1]) < 10:
                    return point
                if abs(x - point[0]) < 10:
                    return (point[0], y)
                if abs(y - point[1]) < 10:
                    return (x, point[1])
        return (x, y)

    # ---------------------- editing / redraw ----------------------
    def undo(self, event=None):
        if not self.lines:
            return
        # remove last line and its label
        last = self.lines.pop()
        if len(last) >= 3 and last[2]:
            try:
                self.canvas.delete(last[2])
            except Exception:
                pass
        self.last_point = self.lines[-1][1] if self.lines else None
        self.redraw_canvas()

    def redraw_canvas(self):
        # clear canvas and draw background & boundary
        self.canvas.delete("all")
        
        # If showing prediction, display it properly
        if self.is_showing_prediction and self.generated_prediction:
            self.display_prediction()
            return
        
        # shade padding area
        w, h = self.canvas_width, self.canvas_height
        p = self.padding
        # top
        self.canvas.create_rectangle(0, 0, w, p, fill="#f5f5f5", width=0)
        # bottom
        self.canvas.create_rectangle(0, h - p, w, h, fill="#f5f5f5", width=0)
        # left
        self.canvas.create_rectangle(0, 0, p, h, fill="#f5f5f5", width=0)
        # right
        self.canvas.create_rectangle(w - p, 0, w, h, fill="#f5f5f5", width=0)

        # draw region boundary
        self.canvas.create_rectangle(*self.draw_region, outline="red", width=2, dash=(4, 2), tags="boundary")

        # optional grid (every 1m)
        try:
            spacing = int(self.scale_var.get())
            if spacing > 5:
                for gx in range(self.draw_region[0], self.draw_region[2], spacing):
                    self.canvas.create_line(gx, self.draw_region[1], gx, self.draw_region[3], fill="#e8e8e8")
                for gy in range(self.draw_region[1], self.draw_region[3], spacing):
                    self.canvas.create_line(self.draw_region[0], gy, self.draw_region[2], gy, fill="#e8e8e8")
        except Exception:
            pass

        # redraw all permanent lines
        for line in self.lines:
            (x1, y1), (x2, y2), label_id = line
            self.canvas.create_line(x1, y1, x2, y2, fill="black", width=2)
            # recreate label text and replace id
            if label_id:
                # compute length again (in case scale changed)
                pixel_length = math.hypot(x2 - x1, y2 - y1)
                scale = float(self.scale_var.get()) if self.scale_var.get() > 0 else 1.0
                real_length = pixel_length / scale
                new_label_id = self.canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2 - 10, text=f"{real_length:.2f} m", fill="blue", font=("Arial", 10))
                # update stored id
                line_index = self.lines.index(line)
                self.lines[line_index] = ((x1, y1), (x2, y2), new_label_id)

        # re-draw internal PIL image to match canvas state
        self.image = Image.new("RGB", (self.canvas_width, self.canvas_height), "white")
        self.image_draw = ImageDraw.Draw(self.image)
        for line in self.lines:
            (x1, y1), (x2, y2), _ = line
            self.image_draw.line((x1, y1, x2, y2), fill=(0, 0, 0), width=2)

    def display_prediction(self):
        """Display the generated prediction properly sized"""
        if not self.generated_prediction:
            return
        
        # Model outputs 64x64, we need to upscale to 400x400 (fixed draw size)
        print(f"Upscaling from {self.generated_prediction.size} to {self.fixed_draw_size}x{self.fixed_draw_size}")
        
        # Upscale 64x64 to 400x400 using best quality interpolation
        resized_pred = self.generated_prediction.resize(
            (self.fixed_draw_size, self.fixed_draw_size), 
            Image.Resampling.LANCZOS
        )
        
        # Create full canvas image with white background
        display_img = Image.new("RGB", (self.canvas_width, self.canvas_height), "white")
        
        # Paste prediction at draw region position (centered)
        display_img.paste(resized_pred, (self.draw_region[0], self.draw_region[1]))
        
        # Update display
        self.tk_image = ImageTk.PhotoImage(display_img)
        self.canvas.create_image(0, 0, image=self.tk_image, anchor=tk.NW)
        self.canvas.update()  # Force canvas update

    # ---------------------- binary mask / generation ----------------------
    def get_binary(self):
        # Create a temporary image for just the draw region
        draw_w = self.draw_region[2] - self.draw_region[0]
        draw_h = self.draw_region[3] - self.draw_region[1]
        temp_img = Image.new("RGB", (draw_w, draw_h), "white")
        temp_draw = ImageDraw.Draw(temp_img)
        
        # Draw all lines adjusted to the draw region coordinates
        for line in self.lines:
            (x1, y1), (x2, y2), _ = line
            # Adjust coordinates relative to draw region
            adj_x1 = x1 - self.draw_region[0]
            adj_y1 = y1 - self.draw_region[1]
            adj_x2 = x2 - self.draw_region[0]
            adj_y2 = y2 - self.draw_region[1]
            temp_draw.line((adj_x1, adj_y1, adj_x2, adj_y2), fill=(0, 0, 0), width=2)
        
        # Close the shape if multiple lines exist
        if len(self.lines) > 1:
            x1, y1 = self.lines[-1][1]
            x2, y2 = self.lines[0][0]
            adj_x1 = x1 - self.draw_region[0]
            adj_y1 = y1 - self.draw_region[1]
            adj_x2 = x2 - self.draw_region[0]
            adj_y2 = y2 - self.draw_region[1]
            temp_draw.line((adj_x1, adj_y1, adj_x2, adj_y2), fill=(0, 0, 0), width=2)

        # Make a copy for floodfill
        fill_image = temp_img.copy()
        
        # Find bbox and perform floodfill
        bbox = fill_image.getbbox()
        if not bbox:
            return None
        
        # Use center of image as seed point
        seed_x = draw_w // 2
        seed_y = draw_h // 2
        
        try:
            ImageDraw.floodfill(fill_image, xy=(seed_x, seed_y), value=(0, 0, 0), border=None)
        except Exception:
            try:
                Image.Image.floodfill(fill_image, (seed_x, seed_y), (0, 0, 0))
            except Exception:
                pass

        # Convert to binary
        gray_image = fill_image.convert("L")
        binary_image = gray_image.point(lambda x: 0 if x > 128 else 255, "1")
        
        # Resize to 64x64 for model input
        resized = binary_image.resize((64, 64), Image.Resampling.BOX)
        self.binary_image = resized
        return resized

    def exit_draw_mode(self, event=None):
        self.drawing_enabled = False
        self.draw_mode_button.config(text="Line")
        self.canvas.delete(self.temp_line_tag)
        self.canvas.delete(self.temp_text_tag)
        self.last_point = None

    def clear_canvas(self):
        self.canvas.delete("all")
        self.lines = []
        self.image = Image.new("RGB", (self.canvas_width, self.canvas_height), "white")
        self.image_draw = ImageDraw.Draw(self.image)
        self.text_input.delete(1.0, tk.END)
        self.exit_draw_mode()
        self.binary_image = None
        self.generated_prediction = None
        self.is_showing_prediction = False
        self.generate_button.config(text="Generate")
        self.regenerate_button.config(state=tk.DISABLED)
        self.save_button.config(state=tk.DISABLED)
        self.redraw_canvas()

    def generate_image(self, repredict=False):
        text = self.text_input.get("1.0", tk.END).strip()
        mask = self.get_binary()
        if mask is None:
            print("Nothing to generate – no drawing detected.")
            return
        
        # Debug: Check mask
        print(f"Mask size: {mask.size}, mode: {mask.mode}")
        mask.save("mask.png")

        ## text generation ###########
        if len(text) < 5:
            new_text = self.text_history[-1] if self.text_history else ""
        else:
            if repredict:
                self.text_history = []
                new_text, mid = prompt2json(text, client=client, model=api_info["model"])
            else:
                if self.generate_button.cget("text") == "Generate":
                    new_text, mid = prompt2json(text, client=client, model=api_info["model"])
                elif self.generate_button.cget("text") == "Edit":
                    new_text, mid = updatePrompt(
                        original_json_str=self.mid,
                        new_description=text,
                        client=client,
                        model=api_info["model"],
                    )
                self.mid = mid
        
        # save new_text as json
        with open("new_text.json", "w") as f:
            f.write(new_text)
        self.text_history.append(new_text)
        ##############################

        print("Starting prediction...")
        # Get prediction from model (should return image matching mask dimensions)
        prediction = self.trainer.predict(mask, new_text, repredict=repredict)
        
        # Debug: Check prediction
        if prediction is None:
            print("ERROR: Model returned None!")
            return
        print(f"Prediction received - Size: {prediction.size}, Mode: {prediction.mode}")
        
        # Store the raw prediction
        self.generated_prediction = prediction
        self.is_showing_prediction = True
        
        # Clear drawing state
        self.lines = []
        self.text_input.delete(1.0, tk.END)
        
        # Display the prediction
        print(f"Draw region: {self.draw_region}")
        print(f"Canvas size: {self.canvas_width}x{self.canvas_height}")
        self.display_prediction()
        
        self.exit_draw_mode()
        self.generate_button.config(text="Edit")
        self.regenerate_button.config(state=tk.NORMAL)
        self.save_button.config(state=tk.NORMAL)
        print("Generation complete!")

    def save_image(self):
        if self.generated_prediction:
            # Save the actual generated prediction at original quality
            self.generated_prediction.save("drawing.png")
        else:
            self.image.save("drawing.png")


if __name__ == '__main__':
    root = tk.Tk()
    app = DrawingApp(root)
    root.mainloop()