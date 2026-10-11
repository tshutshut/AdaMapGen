"""One-page map generator: choose image size, map type, and orientation."""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import secrets
import tkinter as tk
from tkinter import ttk, font as tkfont
import numpy as np
from terrain import MAP_TYPES, generate_map
from brush import paint_stamp, transform_stamp
from regions import build_clean_regions
from settings import AttributePanel
from contour_ui import ContourPanel
from tectonic_ui import TectonicPanel
from topography import contour_colors
from loading import LoadingBar

ASPECT_RATIOS = {'1:1': (1, 1), '4:3': (4, 3), '3:2': (3, 2),
                 '16:9': (16, 9), '21:9': (7, 3), '3:4': (3, 4),
                 '2:3': (2, 3), '9:16': (9, 16), '9:21': (3, 7)}


# What the function does:
#     Uses the exact maximum side length and rounds only the shorter dimension.
# Inputs (if any):
#     ratio: dropdown label; maximum: maximum width or height in cells.
# Outputs (if any):
#     Width and height, both no larger than maximum.
def image_dimensions(ratio, maximum):
    p, q = ASPECT_RATIOS[ratio]
    maximum = int(maximum)
    return (maximum, round(maximum*q/p)) if p >= q else (round(maximum*p/q), maximum)


class Application:
    # What the function does:
    #     Builds the maximized layout, attributes, and brush tabs and starts a map.
    # Inputs (if any):
    #     window: Tk root.
    # Outputs (if any):
    #     A live application with editable presets and a visible seed.
    def __init__(self, window):
        self.window = window
        families = set(tkfont.families(window))
        self.font_family = 'Courier Sans' if 'Courier Sans' in families else 'Courier New'
        # Named fonts cover classic controls, menus, and combobox popups.
        for name in tkfont.names(window):
            tkfont.nametofont(name, root=window).configure(family=self.font_family)
        window.option_add('*Font', 'TkDefaultFont')
        ttk.Style(window).configure('.', font='TkDefaultFont')
        ttk.Style(window).configure('TNotebook.Tab', font='TkDefaultFont')
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = self.pending = self.poll_id = self.queued = None
        self.revision = 0
        self.closed = False
        self.region_executor = ThreadPoolExecutor(max_workers=1)
        self.region_future = self.region_queued = self.region_poll_id = None
        self.region_map = None
        self.region_revision = 0
        self.region_current = False
        self.selected_region = None
        self.stroke_dirty = False
        self.selecting_object = False
        self.strokes = []
        self.undo_stack = []
        self.redo_stack = []
        self.brush_radius = 15
        self.brush_land = True
        self.last_point = None
        self.pointer = None
        self.zoom = 1.
        self.pan = np.zeros(2, dtype=float)
        self.space_down = False
        self.alt_down = False
        self.pan_anchor = None
        self.region_busy = False
        self.turns = 0
        self.kind = MAP_TYPES[0]
        self.seed = secrets.randbits(32)
        window.title('Island Generator')
        window.state('zoomed')
        window.rowconfigure(0, weight=1)
        window.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(window, width=800, height=600, background='#e7e7e7', highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.image_item = self.canvas.create_image(400, 300)
        self.canvas.bind('<Configure>', self.resize_preview)
        panel = ttk.Frame(window, padding=16)
        panel.grid(row=0, column=1, sticky='ns')
        self.notebook = ttk.Notebook(panel)
        self.notebook.pack(fill='x')
        page = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(page, text='1. Layout')
        for column in range(3):
            page.columnconfigure(column, weight=1, uniform='map-buttons')
        self.dimensions = tk.StringVar()
        self.seed_text = tk.StringVar()
        ttk.Label(page, textvariable=self.dimensions, width=44).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 4))
        ttk.Label(page, textvariable=self.seed_text, width=44).grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(0, 12))
        ttk.Label(page, text='Image Size', font=(self.font_family, 11, 'bold')).grid(
            row=2, column=0, columnspan=3, sticky='w')
        self.aspect_ratio = tk.StringVar(value='4:3')
        self.ratio_menu = ttk.Combobox(page, state='readonly', values=list(ASPECT_RATIOS),
                                      textvariable=self.aspect_ratio, width=12)
        self.ratio_menu.grid(row=3, column=0, columnspan=3, sticky='w', pady=8)
        self.ratio_menu.bind('<<ComboboxSelected>>', self.ratio_changed)
        ttk.Label(page, text='Maximum width / height (cells)').grid(
            row=4, column=0, columnspan=3, sticky='w')
        self.maximum = tk.IntVar(value=2048)
        self.size_slider = tk.Scale(page, from_=1024, to=5120, resolution=1, orient='horizontal',
                                    variable=self.maximum, length=380, showvalue=True)
        self.size_slider.grid(row=5, column=0, columnspan=3, sticky='ew')
        self.maximum.trace_add('write', self.schedule_update)
        rotation = ttk.Frame(page)
        rotation.grid(row=6, column=0, columnspan=3, sticky='ew', pady=8)
        rotation.columnconfigure((0, 1), weight=1, uniform='rotation-buttons')
        self.rotation_buttons = []
        for column, (label, direction) in enumerate((('90° CCW', -1), ('90° CW', 1))):
            button = ttk.Button(rotation, text=label, width=12, command=partial(self.rotate, direction))
            button.grid(row=0, column=column, sticky='ew')
            self.rotation_buttons.append(button)
        ttk.Separator(page).grid(row=7, column=0, columnspan=3, sticky='ew', pady=12)
        ttk.Label(page, text='Map type — click for a new variation', font=(self.font_family, 10, 'bold')).grid(
            row=8, column=0, columnspan=3, sticky='w', pady=6)
        self.map_buttons = []
        for column, (kind, label) in enumerate(zip(MAP_TYPES, ('Large island', 'Inland sea', 'Archipelagos'))):
            button = ttk.Button(page, text=label, width=12, command=partial(self.choose_map, kind))
            button.grid(row=9, column=column, sticky='ew', padx=2)
            self.map_buttons.append(button)
        self.layout_page = page
        ttk.Separator(page).grid(row=10, column=0, columnspan=3, sticky='ew', pady=12)
        self.construction_text = tk.StringVar()
        ttk.Label(page, textvariable=self.construction_text,
                  wraplength=380, justify='left').grid(row=11, column=0, columnspan=3, sticky='w', pady=8)
        self.preset_panel = AttributePanel(self.notebook, self.kind, self.schedule_update)
        self.notebook.add(self.preset_panel, text='2. Attributes')
        self.attributes_page = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.attributes_page, text='3. Brush')
        self.brush_loading = LoadingBar(self.attributes_page)
        self.attribute_title = tk.StringVar()
        ttk.Label(self.attributes_page, textvariable=self.attribute_title).pack(anchor='w', pady=8)
        self.brush_text = tk.StringVar()
        ttk.Label(self.attributes_page, textvariable=self.brush_text).pack(anchor='w', pady=12)
        self.zoom_text = tk.StringVar(value='Zoom: 100%')
        navigation = ttk.Frame(self.attributes_page)
        navigation.pack(fill='x', pady=(0, 8))
        ttk.Label(navigation, textvariable=self.zoom_text).pack(side='left')
        ttk.Button(navigation, text='Fit image', command=self.fit_image).pack(side='right')
        ttk.Button(self.attributes_page, text='Clear brush edits', command=self.clear_edits).pack(fill='x')
        ttk.Separator(self.attributes_page).pack(fill='x', pady=12)
        self.region_text = tk.StringVar(value='Regions: updating')
        ttk.Label(self.attributes_page, textvariable=self.region_text).pack(anchor='w')
        self.region_tree = ttk.Treeview(self.attributes_page, columns=('type', 'level', 'parent'),
                                       show='tree headings', height=5, selectmode='browse')
        self.region_tree.heading('#0', text='ID')
        self.region_tree.column('#0', width=65, stretch=False)
        for column, title in (('type', 'Type'), ('level', 'Level'), ('parent', 'Parent')):
            self.region_tree.heading(column, text=title)
            self.region_tree.column(column, width=85, stretch=True)
        self.region_tree.pack(fill='x', pady=6)
        self.region_tree.bind('<<TreeviewSelect>>', self.region_selected)
        self.delete_button = ttk.Button(self.attributes_page, text='Delete selected object',
                                        command=self.delete_region, state='disabled')
        self.delete_button.pack(fill='x')
        history = ttk.Frame(self.attributes_page)
        history.pack(fill='x', pady=6)
        self.undo_button = ttk.Button(history, text='Undo (Ctrl+Z)', command=self.undo, state='disabled')
        self.redo_button = ttk.Button(history, text='Redo (Ctrl+Y)', command=self.redo, state='disabled')
        self.undo_button.pack(side='left', fill='x', expand=True)
        self.redo_button.pack(side='left', fill='x', expand=True)
        self.contour_panel = ContourPanel(self.notebook, self)
        self.notebook.add(self.contour_panel, text='4. Contours')
        self.tectonic_panel = TectonicPanel(self.notebook, self)
        self.notebook.add(self.tectonic_panel, text='5. Tectonics')
        self.brush_cursor = self.canvas.create_oval(0, 0, 0, 0, outline='white', width=2, state='hidden')
        self.canvas.bind('<Motion>', self.brush_motion)
        self.canvas.bind('<Leave>', self.brush_leave)
        self.canvas.bind('<ButtonPress-1>', self.brush_start)
        self.canvas.bind('<Alt-ButtonPress-1>', self.select_canvas_object)
        self.canvas.bind('<B1-Motion>', self.brush_drag)
        self.canvas.bind('<ButtonRelease-1>', self.brush_end)
        self.canvas.bind('<MouseWheel>', self.canvas_wheel)
        window.bind('<KeyPress-space>', self.space_pressed)
        window.bind('<KeyRelease-space>', self.space_released)
        self.canvas.bind('<FocusOut>', self.space_released)
        self.canvas.bind('<FocusOut>', self.alt_released, add='+')
        for key in ('Alt_L', 'Alt_R'):
            window.bind('<KeyPress-'+key+'>', self.alt_pressed)
            window.bind('<KeyRelease-'+key+'>', self.alt_released)
        window.bind('<Up>', self.brush_resize)
        window.bind('<Down>', self.brush_resize)
        for key in ('a', 'A', 'q', 'Q'):
            window.bind('<Control-'+key+'>', self.brush_mode)
        for key in ('z', 'Z'):
            window.bind('<Control-'+key+'>', self.undo)
        for key in ('y', 'Y'):
            window.bind('<Control-'+key+'>', self.redo)
        self.notebook.bind('<<NotebookTabChanged>>', self.attributes_changed)
        self.update_attribute_labels()
        self.status = tk.StringVar()
        # Fixed space prevents progress messages from moving the canvas.
        status_frame = ttk.Frame(panel, width=420, height=65)
        status_frame.pack(fill='x', pady=8)
        status_frame.pack_propagate(False)
        ttk.Label(status_frame, textvariable=self.status, wraplength=410).place(x=0, y=0, width=420, height=65)
        self.left_control = False
        window.bind('<KeyPress-Control_L>', self.control_pressed)
        window.bind('<KeyRelease-Control_L>', self.control_released)
        window.bind('<Control-f>', self.flip_land_sea)
        window.bind('<Control-F>', self.flip_land_sea)
        window.bind('<FocusOut>', self.control_released)
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()
        self.loading_poll_id = None
        self.update_loading()

    # What the function does:
    #     Reflects queued and running work in the same loading strip on tabs 3–5.
    # Inputs (if any):
    #     None; reads existing worker states, including post-Apply object rebuilding.
    # Outputs (if any):
    #     None; schedules a lightweight UI check and avoids duplicate animations.
    def update_loading(self):
        if self.closed:
            return
        shared = (self.region_busy or self.future is not None or self.pending is not None or
                  self.region_future is not None or self.region_queued is not None)
        self.brush_loading.set_busy(shared)
        self.preset_panel.loading_bar.set_busy(shared)
        contour = self.contour_panel
        contour.loading.set_busy(shared or contour.future is not None or contour.pending is not None)
        tectonic = self.tectonic_panel
        tectonic.loading.set_busy(shared or tectonic.future is not None or tectonic.pending is not None or
                                 tectonic.height_future is not None or tectonic.height_pending is not None)
        self.loading_poll_id = self.window.after(80, self.update_loading)

    # What the function does:
    #     Selects a map type and generates a new random variation on every click.
    # Inputs (if any):
    #     kind: one of the three supported map types.
    # Outputs (if any):
    #     None; replaces the seed and resets object IDs, retaining image size.
    def choose_map(self, kind):
        self.zoom = 1.
        self.pan[:] = 0
        self.preset_panel.select_kind(kind)
        self.kind = kind
        self.strokes.clear()
        # A new variation has no object identity relationship to the old map.
        # Clearing the previous region map makes its next counter start at 1.
        self.invalidate_regions()
        self.region_map = None
        self.selected_region = None
        children = self.region_tree.get_children()
        if children:
            self.region_tree.delete(*children)
        self.update_attribute_labels()
        self.seed = secrets.randbits(32)
        self.refresh()

    # What the function does:
    #     Tracks the specifically requested left Control key.
    # Inputs (if any):
    #     event: Tk key event.
    # Outputs (if any):
    #     None.
    def control_pressed(self, event=None):
        self.left_control = True

    # What the function does:
    #     Clears the Control key state on release or loss of focus.
    # Inputs (if any):
    #     event: Tk key/focus event.
    # Outputs (if any):
    #     None.
    def control_released(self, event=None):
        self.left_control = False

    # What the function does:
    #     Exchanges land and sea, switching the island/inland-sea selection.
    # Inputs (if any):
    #     event: optional keyboard event; left Control must be held for shortcuts.
    # Outputs (if any):
    #     'break' for an accepted shortcut; archipelagos remain unchanged.
    def flip_land_sea(self, event=None):
        if event is not None and not self.left_control:
            return None
        if self.kind == 'Many archipelagos':
            return 'break'
        self.kind = 'Inland sea' if self.kind == 'Single large island' else 'Single large island'
        self.preset_panel.select_kind(self.kind, keep_shape=True)
        self.strokes = [transform_stamp(stamp, invert=True) for stamp in self.strokes]
        self.update_attribute_labels()
        # Both generators share a silhouette, so keeping the seed gives the
        # exact complement, including after subsequent resizing and rotation.
        self.refresh()
        return 'break'

    # What the function does:
    #     Applies an aspect-ratio selection as the new base orientation.
    # Inputs (if any):
    #     event: optional dropdown event.
    # Outputs (if any):
    #     None; preserves the seed and regenerates to the new dimensions.
    def ratio_changed(self, event=None):
        self.turns = 0
        self.refresh()

    # What the function does:
    #     Rotates the same seeded map by one quarter turn and swaps its ratio.
    # Inputs (if any):
    #     direction: 1 for clockwise, -1 for counterclockwise.
    # Outputs (if any):
    #     None; queues the rotated version without choosing a new seed.
    def rotate(self, direction):
        self.strokes = [transform_stamp(stamp, direction=direction) for stamp in self.strokes]
        self.turns = (self.turns+direction) % 4
        p, q = ASPECT_RATIOS[self.aspect_ratio.get()]
        self.aspect_ratio.set(next(label for label, pair in ASPECT_RATIOS.items() if pair == (q, p)))
        self.refresh()

    # What the function does:
    #     Coalesces size-slider movement into one background update.
    # Inputs (if any):
    #     args: Tk variable trace arguments.
    # Outputs (if any):
    #     None; schedules regeneration after a short pause.
    def schedule_update(self, *args):
        self.invalidate_regions()
        self.revision += 1
        if self.pending is not None:
            self.window.after_cancel(self.pending)
        self.pending = self.window.after(100, self.refresh)

    # What the function does:
    #     Captures size, type, orientation, and seed for asynchronous generation.
    # Inputs (if any):
    #     None; reads the image controls.
    # Outputs (if any):
    #     None; retains only the latest queued request.
    def refresh(self):
        # History belongs to one generated layout. Edits themselves still replay
        # on regeneration, but older undo states must not restore another layout.
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.stroke_dirty = False
        self.update_edit_buttons()
        self.invalidate_regions()
        if self.pending is not None:
            self.window.after_cancel(self.pending)
            self.pending = None
        width, height = image_dimensions(self.aspect_ratio.get(), self.maximum.get())
        self.dimensions.set(f'{width} × {height} cells')
        self.seed_text.set(f'Seed: {self.seed}')
        self.status.set('Generating Map')
        for button, kind in zip(self.map_buttons, MAP_TYPES):
            button.state(['pressed'] if kind == self.kind else ['!pressed'])
        if self.turns % 2:
            width, height = height, width
        self.revision += 1
        self.queued = (self.revision, self.kind, width, height, self.seed, self.turns, self.preset_panel.snapshot())
        self.start_generation()

    # What the function does:
    #     Starts the latest request when the single background worker is free.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; schedules completion checks on the Tk thread.
    def start_generation(self):
        if self.queued is not None and self.queued[0] != self.revision:
            self.queued = None
        if self.future is None and self.queued is not None:
            self.active, self.queued = self.queued, None
            self.future = self.executor.submit(generate_map, *self.active[1:6], with_geometry=True, preset=self.active[6])
        if self.future is not None and self.poll_id is None:
            self.poll_id = self.window.after(25, self.poll_generation)

    # What the function does:
    #     Shows the latest complete map, discarding obsolete worker results.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; all Tk updates stay on the UI thread.
    def poll_generation(self):
        self.poll_id = None
        if self.closed:
            return
        if self.future is not None and self.future.done():
            future, self.future = self.future, None
            try:
                land, geometry = future.result()
                if self.active[0] == self.revision:
                    self.generated_land, self.geometry = land, geometry
                    self.land = land.copy()
                    for stamp in self.strokes:
                        paint_stamp(self.land, stamp)
                    self.draw()
                    self.status.set('Map Ready')
                    self.update_edit_buttons()
                    self.request_regions()
            except Exception as exc:
                self.status.set(f'Generation failed: {exc}')
        self.start_generation()

    # What the function does:
    #     Renders only the visible portion of the zoomed map, atomically.
    # Inputs (if any):
    #     None; reads the current land mask.
    # Outputs (if any):
    #     None; preserves the previous image until the new one is attached.
    def draw(self):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            cw, ch = 800, 600
        height, width = self.land.shape
        self.view_size = (cw, ch)
        scale = min(cw/width, ch/height)*self.zoom
        w, h = max(1, round(width*scale)), max(1, round(height*scale))
        # Keep smaller dimensions centered; larger dimensions cannot pan beyond
        # the map edge. Allocate a canvas-sized preview even at maximum zoom.
        limit = np.maximum(0, (np.array([w, h])-np.array([cw, ch]))/2)
        self.pan = np.clip(self.pan, -limit, limit)
        left, top = (cw-w)/2+self.pan[0], (ch-h)/2+self.pan[1]
        x0, y0 = max(0, int(np.ceil(left))), max(0, int(np.ceil(top)))
        x1, y1 = min(cw, int(np.ceil(left+w))), min(ch, int(np.ceil(top+h)))
        rows = np.clip(((np.arange(y0, y1)+.5-top)*height/h).astype(int), 0, height-1)
        columns = np.clip(((np.arange(x0, x1)+.5-left)*width/w).astype(int), 0, width-1)
        palette = np.array([[68,126,163], [115,155,93]], dtype=np.uint8)
        contour_view = self.notebook.select() == str(self.contour_panel)
        preview = self.contour_panel.preview_land if contour_view else None
        visible_land = self.land if preview is None else preview
        pixels = palette[visible_land[rows[:, None], columns].astype(np.uint8)]
        contour_labels = []
        self.canvas.delete('height_labels')
        if (self.notebook.select() == str(self.tectonic_panel) and
                self.tectonic_panel.show_elevation.get() and self.tectonic_panel.heightmap is not None):
            heights = self.tectonic_panel.heightmap[rows[:, None], columns]
            on_land = visible_land[rows[:, None], columns]
            elevation_pixels, contour_labels = contour_colors(heights, on_land,
                self.tectonic_panel.contour_interval.get(), self.tectonic_panel.palette.get())
            pixels[on_land] = elevation_pixels[on_land]
        if self.notebook.select() == str(self.tectonic_panel):
            # Trace shared land/sea edges once at display resolution, keeping
            # the aesthetic outline thin at every zoom level. This includes lakes.
            surface = visible_land[rows[:, None], columns]
            outline = np.zeros(surface.shape, dtype=bool)
            outline[:-1, :] |= surface[:-1, :] != surface[1:, :]
            outline[:, :-1] |= surface[:, :-1] != surface[:, 1:]
            # Outline the actual map frame, never the cropped viewport boundary.
            if top >= 0:
                outline[0, :] = True
            if top+h <= ch:
                outline[-1, :] = True
            if left >= 0:
                outline[:, 0] = True
            if left+w <= cw:
                outline[:, -1] = True
            pixels[outline] = [0, 0, 0]
        ids = self.contour_panel.selected if contour_view and not self.contour_panel.all_objects.get() else set()
        if self.brush_enabled() and self.selected_region is not None:
            ids = {self.selected_region}
        if self.region_current and ids and preview is None:
            selected = np.isin(self.region_map.labels[rows[:, None], columns], list(ids))
            # Selection is a preview overlay; the underlying terrain is unchanged.
            pixels[selected] = [255, 0, 0]
        image = tk.PhotoImage(data=f'P6\n{x1-x0} {y1-y0}\n255\n'.encode('ascii')+pixels.tobytes(), format='PPM')
        self.canvas.coords(self.image_item, (x0+x1)/2, (y0+y1)/2)
        self.canvas.itemconfigure(self.image_item, image=image)
        self.image = image
        self.preview_box = (left, top, w, h)
        self.zoom_text.set(f'Zoom: {self.zoom*100:.0f}%')
        self.draw_construction()
        for x, y, value in contour_labels:
            self.canvas.create_text(x0+x, y0+y, text=str(value), fill='#483923',
                                    font=(self.font_family, 9, 'bold'), tags='height_labels')
        self.tectonic_panel.draw()
        self.update_cursor()

    # What the function does:
    #     Zooms around the pointer with Ctrl+wheel; otherwise changes brush size.
    # Inputs (if any):
    #     event: canvas wheel event, including modifier state and coordinates.
    # Outputs (if any):
    #     'break' when handled; zoom never changes map data or brush history.
    def canvas_wheel(self, event):
        if not event.state & 4:
            return self.brush_resize(event)
        if not hasattr(self, 'preview_box') or not event.delta:
            return 'break'
        self.brush_end()
        left, top, w, h = self.preview_box
        u, v = (event.x-left)/w, (event.y-top)/h
        previous = self.zoom
        steps = min(8, max(1, abs(event.delta)//120))
        self.zoom = float(np.clip(self.zoom*1.2**(steps if event.delta > 0 else -steps), 1, 32))
        factor = self.zoom/previous
        cw, ch = self.view_size
        self.pan = np.array([event.x-u*w*factor-(cw-w*factor)/2,
                             event.y-v*h*factor-(ch-h*factor)/2])
        self.draw()
        return 'break'

    # What the function does:
    #     Restores a centered view of the entire image.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; retains terrain and edits.
    def fit_image(self):
        self.brush_end()
        self.zoom = 1.
        self.pan[:] = 0
        if hasattr(self, 'land'):
            self.draw()

    # What the function does:
    #     Temporarily switches to a hand tool while Space is held.
    # Inputs (if any):
    #     event: optional Space key event.
    # Outputs (if any):
    #     'break' to consume the key.
    def space_pressed(self, event=None):
        if not self.space_down:
            self.brush_end()
            self.space_down = True
            self.canvas.configure(cursor='hand2')
            self.update_cursor()
        return 'break'

    # What the function does:
    #     Leaves hand mode on Space release or canvas focus loss.
    # Inputs (if any):
    #     event: optional key or focus event.
    # Outputs (if any):
    #     'break'; resets drag anchors to prevent an accidental paint connection.
    def space_released(self, event=None):
        self.space_down = False
        self.pan_anchor = None
        self.last_point = None
        self.canvas.configure(cursor='')
        self.update_cursor()
        return 'break'

    # What the function does:
    #     Hides the paint cursor and enters selection mode while Alt is held.
    # Inputs (if any):
    #     event: optional Alt key event.
    # Outputs (if any):
    #     'break'; finishes any active stroke before selecting objects.
    def alt_pressed(self, event=None):
        if not self.alt_down:
            self.brush_end()
            self.alt_down = True
        self.update_cursor()
        return 'break'

    # What the function does:
    #     Restores the brush cursor at the real pointer position after Alt release.
    # Inputs (if any):
    #     event: optional Alt release or focus-loss event.
    # Outputs (if any):
    #     'break'; a held selection click still cannot become a paint stroke.
    def alt_released(self, event=None):
        self.alt_down = False
        self.last_point = None
        x, y = self.canvas.winfo_pointerxy()
        self.pointer = (x-self.canvas.winfo_rootx(), y-self.canvas.winfo_rooty())
        self.update_cursor()
        return 'break'

    # What the function does:
    #     Overlays construction on Layout and Attributes while keeping Brush clear.
    # Inputs (if any):
    #     None; uses stored geometry and the fitted preview dimensions.
    # Outputs (if any):
    #     None; canvas guides never modify terrain or painted edits.
    def draw_construction(self):
        self.canvas.delete('construction')
        if self.notebook.select() not in (str(self.layout_page), str(self.preset_panel)) or not hasattr(self, 'geometry'):
            return
        geometry = self.geometry
        if geometry is None:
            return
        left, top, w, h = self.preview_box
        height, width = self.land.shape
        scale = np.array([w/width, h/height])
        offset = np.array([left, top])
        points = geometry.curve*scale+offset
        self.canvas.create_line(*points.ravel().tolist(), fill='#ff55dd', width=2, tags='construction')
        all_points = geometry.points
        colors = ('#ffe16b', '#00ffff', '#ff9933', '#ffffff')
        # Every descendant links to its immediate parent, including deeper levels.
        for child, parent in zip(geometry.children, geometry.parents):
            points = np.array([all_points[parent], child])*scale+offset
            self.canvas.create_line(*points.ravel().tolist(), fill='#aabbcc',
                                    dash=(2, 3), tags='construction')
        # Draw deep points first so the larger root markers remain visible.
        for level in sorted(set(geometry.levels), reverse=True):
            color, radius = colors[min(int(level), 3)], max(1.5, 5-level)
            for x, y in all_points[geometry.levels == level]*scale+offset:
                self.canvas.create_oval(x-radius, y-radius, x+radius, y+radius,
                                        fill=color, outline='#222222', tags='construction')

    # What the function does:
    #     Shows the selected map type and current brush settings.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; updates labels for all map types.
    def update_attribute_labels(self):
        self.attribute_title.set(self.kind)
        self.construction_text.set(
            'Pink: guiding curve\nYellow: cluster seeds\nCyan / orange / white: child levels 1 / 2 / 3\n'
            'Dashed links: child to parent\n\nOriginal generation geometry.')
        self.brush_text.set(f'{"Land (Ctrl+Q)" if self.brush_land else "Sea (Ctrl+A)"} | Diameter: {2*self.brush_radius} px')

    # What the function does:
    #     Reports whether painting is available on the current tab.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     True on the Brush tab for all map types, including archipelagos.
    def brush_enabled(self):
        return self.notebook.select() == str(self.attributes_page)

    # What the function does:
    #     Resets a stroke and refreshes cursor visibility when switching tabs.
    # Inputs (if any):
    #     event: optional tab event.
    # Outputs (if any):
    #     None.
    def attributes_changed(self, event=None):
        self.brush_end()
        self.space_released()
        if self.notebook.select() != str(self.contour_panel):
            self.contour_panel.cancel()
        self.tectonic_panel.enter()
        if hasattr(self, 'land'):
            self.draw()
        self.update_cursor()

    # What the function does:
    #     Selects sea with Ctrl+A or land with Ctrl+Q.
    # Inputs (if any):
    #     event: keyboard event.
    # Outputs (if any):
    #     'break' when handled.
    def brush_mode(self, event):
        if self.brush_enabled() and event.state & 4:
            self.brush_land = event.keysym.lower() == 'q'
            self.update_attribute_labels()
            self.update_cursor()
            return 'break'

    # What the function does:
    #     Changes brush diameter using the wheel or Up/Down arrows.
    # Inputs (if any):
    #     event: mouse wheel or keyboard event.
    # Outputs (if any):
    #     'break' when handled.
    def brush_resize(self, event):
        if not self.brush_enabled():
            return
        increase = event.keysym == 'Up' or getattr(event, 'delta', 0) > 0
        self.brush_radius = max(2, min(150, self.brush_radius+(2 if increase else -2)))
        self.update_attribute_labels()
        self.update_cursor()
        return 'break'

    # What the function does:
    #     Tracks the pointer and displays a circular brush outline.
    # Inputs (if any):
    #     event: canvas pointer position.
    # Outputs (if any):
    #     None.
    def brush_motion(self, event):
        self.pointer = (event.x, event.y)
        self.update_cursor()

    # What the function does:
    #     Hides the brush when the pointer leaves the canvas.
    # Inputs (if any):
    #     event: optional leave event.
    # Outputs (if any):
    #     None.
    def brush_leave(self, event=None):
        self.brush_end()
        self.pointer = self.last_point = None
        self.update_cursor()

    # What the function does:
    #     Displays the brush only over the map and keeps it above the image.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None.
    def update_cursor(self):
        if not hasattr(self, 'brush_cursor'):
            return
        self.canvas.configure(cursor='arrow' if self.alt_down else 'hand2' if self.space_down else '')
        visible = self.brush_enabled() and not self.space_down and not self.alt_down and self.pointer is not None and hasattr(self, 'preview_box')
        if visible:
            x, y = self.pointer
            left, top, w, h = self.preview_box
            visible = (left <= x < left+w and top <= y < top+h and
                       0 <= x < self.view_size[0] and 0 <= y < self.view_size[1])
        self.canvas.itemconfigure(self.brush_cursor, state='normal' if visible else 'hidden')
        if visible:
            r = self.brush_radius
            self.canvas.coords(self.brush_cursor, x-r, y-r, x+r, y+r)
            self.canvas.itemconfigure(self.brush_cursor, outline='#b6ff90' if self.brush_land else '#83dcff')
            self.canvas.tag_raise(self.brush_cursor)

    # What the function does:
    #     Begins a painted stroke on the canvas.
    # Inputs (if any):
    #     event: left-button press.
    # Outputs (if any):
    #     None.
    def brush_start(self, event):
        self.selecting_object = False
        self.canvas.focus_set()
        self.last_point = None
        # Clear the inspector selection only in the empty canvas margin. Use
        # the zoomed image bounds so panning does not change hit-test accuracy.
        if self.brush_enabled() and hasattr(self, 'preview_box'):
            left, top, width, height = self.preview_box
            if not (left <= event.x < left+width and top <= event.y < top+height):
                self.region_tree.selection_remove(*self.region_tree.selection())
                self.selected_region = None
                self.update_edit_buttons()
                self.draw()
        if self.space_down:
            self.pan_anchor = (event.x, event.y)
            return
        if self.notebook.select() == str(self.tectonic_panel):
            self.tectonic_panel.click(event)
            return
        self.brush_drag(event)

    # What the function does:
    #     Paints a continuous chain of circles and stores normalized edit stamps.
    # Inputs (if any):
    #     event: canvas drag position.
    # Outputs (if any):
    #     None; edits remain independent of generated terrain.
    def brush_drag(self, event):
        # An Alt-click remains a selection gesture until the next press, even
        # if Alt is released before the mouse or the pointer leaves the canvas.
        if self.selecting_object:
            self.brush_motion(event)
            return
        if self.alt_down:
            self.brush_motion(event)
            return
        if self.space_down:
            if self.pan_anchor is not None:
                self.pan += np.array([event.x-self.pan_anchor[0], event.y-self.pan_anchor[1]])
                self.pan_anchor = (event.x, event.y)
                if hasattr(self, 'land'):
                    self.draw()
            return
        self.brush_motion(event)
        if not self.brush_enabled() or not hasattr(self, 'preview_box') or self.future is not None or self.pending is not None:
            self.last_point = None
            return
        left, top, w, h = self.preview_box
        x, y = event.x, event.y
        if not (left <= x < left+w and top <= y < top+h):
            self.last_point = None
            return
        previous = self.last_point or (x, y)
        if not self.stroke_dirty:
            self.remember_edit()
        self.invalidate_regions()
        self.stroke_dirty = True
        count = max(1, int(np.hypot(x-previous[0], y-previous[1])/max(1, self.brush_radius/2))+1)
        for sx, sy in zip(np.linspace(previous[0], x, count), np.linspace(previous[1], y, count)):
            stamp = ((sx-left)/w, (sy-top)/h, self.brush_radius/min(w, h), self.brush_land)
            self.strokes.append(stamp)
            paint_stamp(self.land, stamp)
        self.last_point = (x, y)
        self.draw()

    # What the function does:
    #     Ends the active stroke so the next click starts independently.
    # Inputs (if any):
    #     event: optional mouse release.
    # Outputs (if any):
    #     None.
    def brush_end(self, event=None):
        self.pan_anchor = None
        self.last_point = None
        if self.stroke_dirty:
            self.stroke_dirty = False
            self.request_regions()

    # What the function does:
    #     Removes only manual painting, restoring the latest generated terrain.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None.
    def clear_edits(self):
        if self.future is not None or self.pending is not None or not self.strokes:
            return
        self.brush_end()
        self.remember_edit()
        self.strokes.clear()
        if hasattr(self, 'generated_land'):
            self.land = self.generated_land.copy()
            self.stroke_dirty = False
            self.invalidate_regions()
            self.draw()
            self.request_regions()

    # What the function does:
    #     Prevents outdated region analysis from being displayed after an edit.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; keeps old objects only for matching IDs on the next analysis.
    def invalidate_regions(self):
        self.tectonic_panel.invalidate()
        self.region_revision += 1
        self.region_current = False
        self.contour_panel.selected.clear()
        self.contour_panel.selection_changed()
        self.region_queued = None
        self.region_text.set('Recalculating objects…')
        if not self.region_busy:
            self.region_busy = True
        self.update_edit_buttons()

    # What the function does:
    #     Queues a snapshot once painting or map generation finishes.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; background work never reads the actively painted array.
    def request_regions(self):
        self.region_queued = (self.region_revision, self.land.copy(), self.region_map)
        self.poll_regions()

    # What the function does:
    #     Collects region objects and starts only the latest pending analysis.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; updates the inspector on the Tk thread.
    def poll_regions(self):
        if self.region_poll_id is not None:
            self.window.after_cancel(self.region_poll_id)
            self.region_poll_id = None
        if self.closed:
            return
        if self.region_future is not None and self.region_future.done():
            future, self.region_future = self.region_future, None
            try:
                result, cleanup_edits = future.result()
                if self.region_active == self.region_revision:
                    # Cleanup belongs to the action that produced this snapshot;
                    # it does not create a separate undo step. Stale jobs never
                    # append edits to a newer map.
                    self.strokes.extend(cleanup_edits)
                    self.region_map = result
                    self.region_current = True
                    # Render from exact cell ownership: nested regions cannot
                    # accidentally cover their children or neighboring objects.
                    self.land = result.render()
                    children = self.region_tree.get_children()
                    if children:
                        self.region_tree.delete(*children)
                    for obj in sorted(result.objects.values(), key=lambda obj: (obj.level, obj.id)):
                        self.region_tree.insert('', 'end', iid=str(obj.id), text=str(obj.id),
                                                values=(obj.kind, obj.level, obj.parent or '—'))
                    land_count = sum(obj.kind == 'land' for obj in result.objects.values())
                    self.region_text.set(f'Regions: {land_count} land / {len(result.objects)-land_count} sea')
                    self.region_busy = False
                    self.tectonic_panel.enter()
                    if self.contour_panel.message.get().startswith('Applied.'):
                        self.contour_panel.message.set('Applied. Objects are ready.')
                    if self.selected_region in result.objects:
                        self.region_tree.selection_set(str(self.selected_region))
                    else:
                        self.selected_region = None
                    self.draw()
                    self.update_edit_buttons()
            except Exception as exc:
                self.region_text.set(f'Region analysis failed: {exc}')
                self.region_busy = False
        if self.region_future is None and self.region_queued is not None:
            revision, snapshot, previous = self.region_queued
            self.region_queued = None
            self.region_active = revision
            self.region_future = self.region_executor.submit(build_clean_regions, snapshot, previous)
        if self.region_future is not None:
            self.region_poll_id = self.window.after(40, self.poll_regions)

    # What the function does:
    #     Selects the region under an Alt-click without starting a paint stroke.
    # Inputs (if any):
    #     event: Alt + left-button press in canvas coordinates.
    # Outputs (if any):
    #     'break'; synchronizes the object list and red preview highlight.
    def select_canvas_object(self, event):
        self.brush_end()
        self.alt_pressed()
        self.brush_motion(event)
        self.selecting_object = True
        self.canvas.focus_set()
        contour_view = self.notebook.select() == str(self.contour_panel)
        if not (self.brush_enabled() or contour_view) or not hasattr(self, 'preview_box'):
            return 'break'
        left, top, w, h = self.preview_box
        if not (left <= event.x < left+w and top <= event.y < top+h):
            if contour_view:
                self.contour_panel.selected.clear()
                self.contour_panel.selection_changed()
                return 'break'
            self.region_tree.selection_remove(*self.region_tree.selection())
            self.region_selected()
            return 'break'
        # Never select from an outdated label image while objects are rebuilding.
        if not self.region_current:
            return 'break'
        height, width = self.land.shape
        x = min(width-1, int((event.x+.5-left)*width/w))
        y = min(height-1, int((event.y+.5-top)*height/h))
        identity = str(int(self.region_map.labels[y, x]))
        if contour_view:
            self.contour_panel.toggle(int(identity))
            return 'break'
        self.region_tree.selection_set(identity)
        self.region_tree.see(identity)
        self.region_selected()
        return 'break'

    # What the function does:
    #     Highlights the region selected in the inspector.
    # Inputs (if any):
    #     event: optional Treeview selection event.
    # Outputs (if any):
    #     None; the highlight changes only the preview, never the terrain.
    def region_selected(self, event=None):
        selection = self.region_tree.selection()
        self.selected_region = int(selection[0]) if selection else None
        self.update_edit_buttons()
        if hasattr(self, 'land'):
            self.draw()

    # What the function does:
    #     Enables edit actions only when their required state is available.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; updates button states.
    def update_edit_buttons(self):
        ready = self.future is None and self.pending is None
        self.delete_button.state(['!disabled'] if ready and self.region_current and
                                 self.selected_region in self.region_map.objects else ['disabled'])
        self.undo_button.state(['!disabled'] if ready and self.undo_stack else ['disabled'])
        self.redo_button.state(['!disabled'] if ready and self.redo_stack else ['disabled'])

    # What the function does:
    #     Saves one undo point and discards the abandoned redo branch.
    # Inputs (if any):
    #     None; saved edits are immutable and can share packed masks.
    # Outputs (if any):
    #     None; retains at most 50 edit actions.
    def remember_edit(self):
        self.undo_stack.append(list(self.strokes))
        self.undo_stack = self.undo_stack[-50:]
        self.redo_stack.clear()
        self.update_edit_buttons()

    # What the function does:
    #     Deletes only the selected region by replacing its cells with the other type.
    # Inputs (if any):
    #     None; reads the inspector selection.
    # Outputs (if any):
    #     None; nested objects retain their cells and regions are rebuilt.
    def delete_region(self):
        if not self.region_current or self.selected_region not in self.region_map.objects:
            return
        if self.future is not None or self.pending is not None:
            return
        self.remember_edit()
        region = self.region_map.objects[self.selected_region]
        mask = self.region_map.labels == region.id
        stamp = {'mask': np.packbits(mask), 'shape': mask.shape, 'value': region.kind == 'sea'}
        self.strokes.append(stamp)
        paint_stamp(self.land, stamp)
        self.selected_region = None
        self.invalidate_regions()
        self.draw()
        self.request_regions()

    # What the function does:
    #     Restores the previous brush/deletion state as one complete action.
    # Inputs (if any):
    #     event: optional Ctrl+Z event.
    # Outputs (if any):
    #     'break' to consume the shortcut.
    def undo(self, event=None):
        return self.restore_edit(self.undo_stack, self.redo_stack)

    # What the function does:
    #     Reapplies the most recently undone editing action.
    # Inputs (if any):
    #     event: optional Ctrl+Y event.
    # Outputs (if any):
    #     'break' to consume the shortcut.
    def redo(self, event=None):
        return self.restore_edit(self.redo_stack, self.undo_stack)

    # What the function does:
    #     Transfers an edit state between history stacks and rebuilds the map.
    # Inputs (if any):
    #     source: history to restore; destination: history receiving current state.
    # Outputs (if any):
    #     'break'; no action during pending generation or with empty history.
    def restore_edit(self, source, destination):
        if self.future is not None or self.pending is not None or not source:
            return 'break'
        self.stroke_dirty = False
        self.last_point = None
        destination.append(list(self.strokes))
        self.strokes = source.pop()
        self.land = self.generated_land.copy()
        for stamp in self.strokes:
            paint_stamp(self.land, stamp)
        self.invalidate_regions()
        self.draw()
        self.request_regions()
        return 'break'

    # What the function does:
    #     Refits the visible map when the window is resized.
    # Inputs (if any):
    #     event: optional Tk resize event.
    # Outputs (if any):
    #     None.
    def resize_preview(self, event=None):
        if hasattr(self, 'land'):
            self.draw()

    # What the function does:
    #     Cancels callbacks and closes the application safely.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None.
    def close(self):
        self.closed = True
        self.contour_panel.close()
        self.tectonic_panel.close()
        for bar in (self.preset_panel.loading_bar, self.brush_loading, self.contour_panel.loading, self.tectonic_panel.loading):
            bar.set_busy(False)
        for callback in (self.pending, self.poll_id, self.region_poll_id, self.loading_poll_id):
            if callback is not None:
                self.window.after_cancel(callback)
        self.queued = None
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.region_executor.shutdown(wait=False, cancel_futures=True)
        self.window.destroy()


# What the function does:
#     Starts the map generator desktop application.
# Inputs (if any):
#     None.
# Outputs (if any):
#     None; returns when the window closes.
def main():
    root = tk.Tk()
    Application(root)
    root.mainloop()


if __name__ == '__main__':
    main()
