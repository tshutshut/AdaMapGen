"""Fault-network controls and canvas overlays."""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import tkinter as tk
from tkinter import ttk
import numpy as np
from tectonics import generate_faults, FaultNetwork
from elevation import ElevationSettings, generate_elevation
from topography import PALETTES
from loading import LoadingBar


class TectonicPanel(ttk.Frame):
    # What the function does:
    #     Creates network controls and a movement selector for individual faults.
    # Inputs (if any):
    #     parent: notebook; app: map application.
    # Outputs (if any):
    #     A tectonics tab with a background graph generator.
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.loading = LoadingBar(self)
        self.app = app
        self.network = None
        self.selected = None
        self.revision = 0
        self.future = self.pending = self.poll_id = None
        self.requested = False
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.heightmap = None
        self.height_future = self.height_pending = self.height_poll_id = None
        self.height_revision = 0
        self.height_requested = False
        self.show_faults = tk.BooleanVar(value=True)
        self.show_elevation = tk.BooleanVar(value=True)
        visibility = ttk.Frame(self)
        visibility.pack(fill='x', pady=(0, 8))
        ttk.Checkbutton(visibility, text='Faults / arrows', variable=self.show_faults,
                        command=self.refresh_view).pack(side='left')
        ttk.Checkbutton(visibility, text='Elevation', variable=self.show_elevation,
                        command=self.refresh_view).pack(side='left')
        palette_row = ttk.Frame(self)
        palette_row.pack(fill='x', pady=(0, 8))
        ttk.Label(palette_row, text='Elevation palette').pack(side='left')
        self.palette = tk.StringVar(value='Terrain')
        self.palette_menu = ttk.Combobox(palette_row, textvariable=self.palette,
                                         values=tuple(PALETTES), state='readonly', width=16)
        self.palette_menu.pack(side='right')
        self.palette_menu.bind('<<ComboboxSelected>>', self.refresh_view)
        sections = ttk.Notebook(self)
        sections.pack(fill='both', expand=True)
        network_page = ttk.Frame(sections, padding=6)
        elevation_page = ttk.Frame(sections, padding=6)
        sections.add(network_page, text='Network')
        sections.add(elevation_page, text='Elevation')
        self.values = []
        for label, low, high, step, default in (
                ('Interior junctions', 2, 60, 1, 20), ('Coastline endpoints', 2, 40, 1, 12),
                ('Minimum spacing (% of shorter side)', 1, 15, .5, 4), ('Network seed', 0, 99999, 1, 1)):
            ttk.Label(network_page, text=f'{label} [{low:g}–{high:g}]').pack(anchor='w', pady=(8, 0))
            value = tk.DoubleVar(value=default)
            slider = tk.Scale(network_page, from_=low, to=high, resolution=step, variable=value,
                              orient='horizontal', length=380)
            slider.pack(fill='x')
            slider.bind('<MouseWheel>', partial(self.wheel, slider, value))
            self.values.append(value)
            value.trace_add('write', self.settings_changed)
        # Network sliders already regenerate automatically; no extra button needed.
        self.height_viewport = tk.Canvas(elevation_page, height=340, width=390, highlightthickness=0)
        scrollbar = ttk.Scrollbar(elevation_page, orient='vertical', command=self.height_viewport.yview)
        self.height_viewport.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.height_viewport.pack(side='left', fill='both', expand=True)
        body = ttk.Frame(self.height_viewport)
        self.height_body = body
        self.height_item = self.height_viewport.create_window(0, 0, window=body, anchor='nw')
        body.bind('<Configure>', self.resize_height_content)
        self.height_viewport.bind('<Configure>', self.resize_height_view)
        self.height_viewport.bind('<MouseWheel>', self.scroll_height)
        body.bind('<MouseWheel>', self.scroll_height)
        self.height_values = {}
        self.height_sliders = {}
        defaults = ElevationSettings()
        for name, label, low, high, step in (
                ('base', 'Base land elevation', 0, 2000, 25),
                ('max_height', 'Maximum height per peak', 0, 5000, 50),
                ('valley_depth', 'Maximum depth per valley', 0, 2000, 25),
                ('peaks', 'Peaks per fault', 1, 12, 1),
                ('count_variation', 'Peak count variation (+/-)', 0, 5, 1),
                ('height_variation', 'Height variation (fraction)', 0, 1, .05),
                ('width', 'Ridge width (% of shorter side)', .2, 8, .1),
                ('width_variation', 'Width variation (+/- fraction)', 0, .75, .05),
                ('steepness', 'Steepness (2 = Gaussian)', 1, 4, .1),
                ('ruggedness', 'Ridge ruggedness', 0, 1, .05),
                ('seed', 'Elevation seed', 0, 99999, 1)):
            label_widget = ttk.Label(body, text=f'{label} [{low:g}–{high:g}]')
            label_widget.pack(anchor='w', pady=(8, 0))
            value = tk.DoubleVar(value=getattr(defaults, name))
            slider = tk.Scale(body, from_=low, to=high, resolution=step, variable=value,
                              orient='horizontal', length=370)
            slider.pack(fill='x')
            slider.bind('<MouseWheel>', partial(self.wheel, slider, value))
            label_widget.bind('<MouseWheel>', partial(self.wheel, slider, value))
            self.height_values[name] = value
            self.height_sliders[name] = slider
            value.trace_add('write', self.height_changed)
        ttk.Label(body, text='Contour interval [25–1000 elevation units]').pack(anchor='w', pady=(8, 0))
        self.contour_interval = tk.DoubleVar(value=100)
        interval_slider = tk.Scale(body, from_=25, to=1000, resolution=25, variable=self.contour_interval,
                                   orient='horizontal', length=370)
        interval_slider.pack(fill='x')
        interval_slider.bind('<MouseWheel>', partial(self.wheel, interval_slider, self.contour_interval))
        self.contour_interval.trace_add('write', self.refresh_view)
        ttk.Separator(self).pack(fill='x', pady=8)
        self.selection_text = tk.StringVar(value='Click a fault line to select it.')
        ttk.Label(self, textvariable=self.selection_text).pack(anchor='w', pady=8)
        self.mode = tk.StringVar(value='Convergence')
        self.mode_menu = ttk.Combobox(self, textvariable=self.mode, state='disabled',
                                     values=('Convergence', 'Divergence', 'Inactive'))
        self.mode_menu.pack(fill='x')
        self.mode_menu.bind('<<ComboboxSelected>>', self.change_mode)
        ttk.Label(self, text='Red: mountains · Blue: valleys · Gray: inactive\n'
                  'Elevation bands with contour lines.\nEvery fifth contour is darker and labelled.\n'
                  'Valleys stop at zero; coastlines stay fixed.',
                  wraplength=390).pack(anchor='w', pady=12)
        self.message = tk.StringVar(value='Open this tab to generate the network.')
        ttk.Label(self, textvariable=self.message, wraplength=390).pack(anchor='w')
        self.height_message = tk.StringVar(value='Elevation will follow the fault network.')
        ttk.Label(self, textvariable=self.height_message, wraplength=390).pack(anchor='w', pady=4)

    # What the function does:
    #     Updates the elevation controls' scrolling bounds.
    # Inputs (if any):
    #     event: optional frame configure event.
    # Outputs (if any):
    #     args: optional display-control trace arguments.
    def resize_height_content(self, event=None):
        self.height_viewport.configure(scrollregion=self.height_viewport.bbox('all'))

    # What the function does:
    #     Fits the elevation controls to the sidebar width.
    # Inputs (if any):
    #     event: viewport resize event.
    # Outputs (if any):
    #     None.
    def resize_height_view(self, event):
        self.height_viewport.itemconfigure(self.height_item, width=event.width)

    # What the function does:
    #     Scrolls elevation controls when the pointer is outside a slider.
    # Inputs (if any):
    #     event: wheel event.
    # Outputs (if any):
    #     'break'.
    def scroll_height(self, event):
        if event.delta:
            self.height_viewport.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    # What the function does:
    #     Updates visibility without recomputing either terrain or elevation.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None.
    def refresh_view(self, *args):
        if hasattr(self.app, 'land'):
            self.app.draw()

    # What the function does:
    #     Debounces changes to elevation settings or fault movement.
    # Inputs (if any):
    #     args: optional Tk trace data.
    # Outputs (if any):
    #     None; retains the old overlay until a current replacement is ready.
    def height_changed(self, *args):
        maximum_variation = min(5, int(self.height_values['peaks'].get())-1)
        self.height_sliders['count_variation'].configure(to=maximum_variation)
        if self.height_values['count_variation'].get() > maximum_variation:
            self.height_values['count_variation'].set(maximum_variation)
            return
        self.height_revision += 1
        self.height_requested = True
        if self.height_pending is not None:
            self.after_cancel(self.height_pending)
        self.height_pending = self.after(180, self.generate_height)
        self.height_message.set('Updating elevation…')

    # What the function does:
    #     Computes additive elevations from a frozen copy of fault modes and settings.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; expensive calculations run on the background worker.
    def generate_height(self):
        if self.height_pending is not None:
            self.after_cancel(self.height_pending)
            self.height_pending = None
        if self.network is None:
            return
        if self.height_future is not None:
            self.height_requested = True
            return
        self.height_requested = False
        values = {name: value.get() for name, value in self.height_values.items()}
        for name in ('peaks', 'count_variation', 'seed'):
            values[name] = int(values[name])
        self.height_active = self.height_revision
        network = FaultNetwork(self.network.points.copy(), self.network.coastal.copy(),
                               list(self.network.edges), list(self.network.modes))
        self.height_future = self.executor.submit(generate_elevation, self.app.land.copy(), network, ElevationSettings(**values))
        self.height_poll_id = self.after(40, self.poll_height)

    # What the function does:
    #     Installs the newest heightmap and reports its actual elevation range.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; obsolete results never replace current terrain.
    def poll_height(self):
        self.height_poll_id = None
        if not self.height_future.done():
            self.height_poll_id = self.after(40, self.poll_height)
            return
        future, self.height_future = self.height_future, None
        try:
            heights = future.result()
            if self.height_active == self.height_revision:
                self.heightmap = heights
                values = heights[self.app.land]
                self.height_message.set(f'Land elevation: {values.min():.0f}–{values.max():.0f}' if values.size else 'No land to elevate.')
                self.refresh_view()
        except Exception as exc:
            if self.height_active == self.height_revision:
                self.height_message.set(f'Elevation failed: {exc}')
        if self.height_requested and self.height_pending is None:
            self.generate_height()

    # What the function does:
    #     Adjusts a hovered generation slider by one step.
    # Inputs (if any):
    #     slider/value: target; event: wheel event.
    # Outputs (if any):
    #     'break'.
    def wheel(self, slider, value, event):
        if event.delta:
            value.set(float(np.clip(value.get()+float(slider.cget('resolution'))*(1 if event.delta > 0 else -1),
                                    float(slider.cget('from')), float(slider.cget('to')))))
        return 'break'

    # What the function does:
    #     Discards stale graph geometry and fault selections after terrain changes.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; obsolete worker results will not be shown.
    def invalidate(self):
        self.height_revision += 1
        self.height_requested = False
        self.heightmap = None
        if self.height_pending is not None:
            self.after_cancel(self.height_pending)
            self.height_pending = None
        self.revision += 1
        self.network = None
        self.selected = None
        self.requested = False
        if self.pending is not None:
            self.after_cancel(self.pending)
            self.pending = None
        self.mode_menu.configure(state='disabled')
        self.selection_text.set('Click a fault line to select it.')
        self.message.set('Network will regenerate for the updated terrain.')
        self.app.canvas.delete('tectonics')

    # What the function does:
    #     Debounces generation sliders so only their latest values are used.
    # Inputs (if any):
    #     args: Tk variable trace data.
    # Outputs (if any):
    #     None.
    def settings_changed(self, *args):
        self.invalidate()
        self.pending = self.after(180, self.generate)

    # What the function does:
    #     Ensures the active tectonics tab has a graph for the committed terrain.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; waits for region analysis before starting.
    def enter(self):
        if self.app.notebook.select() == str(self) and self.network is None and self.future is None and self.pending is None:
            self.generate()

    # What the function does:
    #     Starts a background graph calculation on a frozen map snapshot.
    # Inputs (if any):
    #     None; reads generation sliders.
    # Outputs (if any):
    #     None.
    def generate(self):
        if self.pending is not None:
            self.after_cancel(self.pending)
            self.pending = None
        if not self.app.region_current:
            self.message.set('Waiting for object recalculation…')
            return
        if self.future is not None:
            self.requested = True
            return
        self.requested = False
        self.active = self.revision
        count, endpoints, spacing, seed = [v.get() for v in self.values]
        self.future = self.executor.submit(generate_faults, self.app.land.copy(), self.app.region_map.labels,
                                           int(count), int(endpoints), spacing, int(seed))
        self.message.set('Generating fault network…')
        self.poll_id = self.after(40, self.poll)

    # What the function does:
    #     Accepts only current graph results and updates the overlay.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None.
    def poll(self):
        self.poll_id = None
        if not self.future.done():
            self.poll_id = self.after(40, self.poll)
            return
        future, self.future = self.future, None
        try:
            network = future.result()
            if self.active == self.revision:
                self.network = network
                self.selected = None
                self.mode_menu.configure(state='disabled')
                self.selection_text.set('Click a fault line to select it.')
                self.message.set(f'{int((~network.coastal).sum())} junctions, {int(network.coastal.sum())} endpoints, '
                                 f'{len(network.edges)} faults.\nOnly placements and connections that fit on land are kept.')
                self.draw()
                self.height_changed()
        except Exception as exc:
            self.message.set(f'Network generation failed: {exc}')
        if self.requested and self.pending is None:
            self.generate()
        elif self.active != self.revision:
            self.enter()

    # What the function does:
    #     Converts graph coordinates into the current zoomed/panned canvas view.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     Canvas point coordinates.
    def screen_points(self):
        left, top, w, h = self.app.preview_box
        height, width = self.app.land.shape
        return self.network.points*np.array([w/width, h/height])+np.array([left, top])

    # What the function does:
    #     Draws faults, junctions, and paired perpendicular movement arrows.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; this overlay never paints or changes terrain.
    def draw(self):
        canvas = self.app.canvas
        canvas.delete('tectonics')
        if (self.app.notebook.select() != str(self) or self.network is None or
                not self.show_faults.get() or not hasattr(self.app, 'preview_box')):
            return
        points = self.screen_points()
        colors = {'Convergence': '#e84343', 'Divergence': '#39baff', 'Inactive': '#888888'}
        for index, (i, j) in enumerate(self.network.edges):
            a, b = points[[i, j]]
            mode = self.network.modes[index]
            if index == self.selected:
                canvas.create_line(*a, *b, fill='white', width=7, tags='tectonics')
            canvas.create_line(*a, *b, fill=colors[mode], width=3, tags='tectonics')
            tangent = b-a
            length = np.linalg.norm(tangent)
            normal = np.array([-tangent[1], tangent[0]])/max(length, 1e-6)
            midpoint = (a+b)/2
            if mode != 'Inactive':
                for side in (-1, 1):
                    near, far = midpoint+side*normal*5, midpoint+side*normal*23
                    start, end = (far, near) if mode == 'Convergence' else (near, far)
                    canvas.create_line(*start, *end, fill=colors[mode], width=2,
                                       arrow='last', arrowshape=(8, 10, 4), tags='tectonics')
        for (x, y), coast in zip(points, self.network.coastal):
            canvas.create_oval(x-4, y-4, x+4, y+4, fill='#00ffff' if coast else '#ffe16b',
                               outline='#222222', tags='tectonics')

    # What the function does:
    #     Selects the closest fault within eight display pixels of a left-click.
    # Inputs (if any):
    #     event: canvas click.
    # Outputs (if any):
    #     None; empty-space clicks deselect the current fault.
    def click(self, event):
        self.selected = None
        if self.network is not None and self.show_faults.get():
            points = self.screen_points()
            best = 8.
            for index, (i, j) in enumerate(self.network.edges):
                a, b = points[[i, j]]
                delta = b-a
                t = np.clip(np.dot(np.array([event.x, event.y])-a, delta)/max(np.dot(delta, delta), 1e-9), 0, 1)
                distance = np.linalg.norm(np.array([event.x, event.y])-(a+t*delta))
                if distance < best:
                    best, self.selected = distance, index
        self.mode_menu.configure(state='readonly' if self.selected is not None else 'disabled')
        self.selection_text.set(f'Fault {self.selected+1}' if self.selected is not None else 'Click a fault line to select it.')
        if self.selected is not None:
            self.mode.set(self.network.modes[self.selected])
        self.draw()

    # What the function does:
    #     Changes the selected fault's movement and redraws its arrows.
    # Inputs (if any):
    #     event: optional combobox event.
    # Outputs (if any):
    #     None; rebuilds the elevation overlay using the new movement type.
    def change_mode(self, event=None):
        if self.network is not None and self.selected is not None:
            self.network.modes[self.selected] = self.mode.get()
            self.draw()
            self.height_changed()

    # What the function does:
    #     Cancels pending UI callbacks and shuts down the graph worker.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None.
    def close(self):
        for callback in (self.pending, self.poll_id, self.height_pending, self.height_poll_id):
            if callback is not None:
                self.after_cancel(callback)
        self.executor.shutdown(wait=False, cancel_futures=True)
