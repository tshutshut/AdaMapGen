"""Scrollable preset controls with local mouse-wheel adjustment."""
from dataclasses import replace
from functools import partial
import tkinter as tk
from tkinter import ttk, font as tkfont
from terrain import PRESETS
from loading import LoadingBar

# Each definition includes its label, limits, and wheel/keyboard step.
# Ratios are multipliers, not percentages of the image dimensions.
CONTROLS = (
    ('Curve', 'curve_scale', 'Curve size', .4, 1.2, .01),
    ('Curve', 'curve_waviness', 'Curve waviness', 0, 1.5, .01),
    ('Main seeds', 'cluster_min', 'Minimum seeds', 3, 16, 1),
    ('Main seeds', 'cluster_max', 'Maximum seeds', 3, 16, 1),
    ('Main seeds', 'seed_jitter', 'Spacing variation', 0, .45, .01),
    ('Main seeds', 'open_end_margin', 'Open-curve end margin', 0, .3, .01),
    ('Children', 'levels', 'Child levels', 1, 3, 1),
    ('Children', 'child_min', 'Minimum children / parent', 1, 5, 1),
    ('Children', 'child_max', 'Maximum children / parent', 1, 5, 1),
    ('Children', 'child_angle_variation', 'Child angle variation (± degrees)', 0, 180, 5),
    ('Distances', 'distance_1', 'Level 1 / seed spacing', .05, .9, .01),
    ('Distances', 'distance_2', 'Level 2 / level 1 distance', .05, .9, .01),
    ('Distances', 'distance_3', 'Level 3 / level 2 distance', .05, .9, .01),
    ('Blob sizes', 'blob_radius_ratio', 'Root radius / seed spacing', .05, .5, .01),
    ('Blob sizes', 'blob_size_variation', 'Radius variation (+/-)', 0, .35, .01),
    ('Blob sizes', 'shrink_1', 'Level 1 / parent radius', .1, .7, .01),
    ('Blob sizes', 'shrink_2', 'Level 2 / parent radius', .1, .7, .01),
    ('Blob sizes', 'shrink_3', 'Level 3 / parent radius', .1, .7, .01),
)


class AttributePanel(ttk.Frame):
    # What the function does:
    #     Builds grouped sliders with scrolling and hover-based wheel control.
    # Inputs (if any):
    #     parent: notebook; kind: initial preset; changed: regeneration callback.
    # Outputs (if any):
    #     An attributes frame containing all supported generation settings.
    def __init__(self, parent, kind, changed):
        super().__init__(parent, padding=12)
        self.loading_bar = LoadingBar(self)
        self.kind, self.changed = kind, changed
        self.loading = True
        self.saved = dict(PRESETS)
        self.values, self.sliders, self.labels = {}, {}, {}
        self.title = tk.StringVar()
        ttk.Label(self, textvariable=self.title).pack(anchor='w', pady=(0, 8))
        ttk.Button(self, text='Reset preset attributes', command=self.reset).pack(fill='x', pady=(0, 8))
        container = ttk.Frame(self)
        container.pack(fill='both', expand=True)
        self.viewport = tk.Canvas(container, width=410, height=430, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient='vertical', command=self.viewport.yview)
        self.viewport.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.viewport.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(self.viewport)
        self.body_item = self.viewport.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', self.resize_content)
        self.viewport.bind('<Configure>', self.resize_viewport)
        self.viewport.bind('<MouseWheel>', self.scroll)
        self.body.bind('<MouseWheel>', self.scroll)
        section = None
        for group, name, label, low, high, step in CONTROLS:
            if group != section:
                heading = ttk.Label(self.body, text=group,
                                    font=(tkfont.nametofont('TkDefaultFont').actual('family'), 10, 'bold'))
                heading.pack(anchor='w', pady=(12, 4))
                heading.bind('<MouseWheel>', self.scroll)
                section = group
            row = ttk.Frame(self.body)
            row.pack(fill='x', pady=2)
            caption = ttk.Label(row, text=f'{label}  [{low:g}–{high:g}]')
            caption.pack(anchor='w')
            value = tk.DoubleVar(value=low)
            slider = tk.Scale(row, from_=low, to=high, resolution=step, variable=value,
                              orient='horizontal', length=370, takefocus=True)
            slider.pack(fill='x')
            self.values[name], self.sliders[name], self.labels[name] = value, slider, caption
            for widget in (row, caption, slider):
                widget.bind('<MouseWheel>', partial(self.wheel, name))
            caption.bind('<Button-1>', partial(self.focus_slider, name))
            value.trace_add('write', partial(self.value_changed, name))
        self.load(PRESETS[kind])

    # What the function does:
    #     Keeps the scrollable area sized to its controls.
    # Inputs (if any):
    #     event: frame resize event.
    # Outputs (if any):
    #     None.
    def resize_content(self, event=None):
        self.viewport.configure(scrollregion=self.viewport.bbox('all'))

    # What the function does:
    #     Fits attribute rows to the available sidebar width.
    # Inputs (if any):
    #     event: viewport resize event.
    # Outputs (if any):
    #     None.
    def resize_viewport(self, event):
        self.viewport.itemconfigure(self.body_item, width=event.width)

    # What the function does:
    #     Scrolls the list when the pointer is outside a specific attribute.
    # Inputs (if any):
    #     event: mouse wheel event.
    # Outputs (if any):
    #     'break' so the brush and other widgets cannot consume this wheel event.
    def scroll(self, event):
        if event.delta:
            self.viewport.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    # What the function does:
    #     Focuses a slider when its caption is clicked, enabling arrow-key edits.
    # Inputs (if any):
    #     name: setting key; event: click event.
    # Outputs (if any):
    #     None.
    def focus_slider(self, name, event=None):
        self.sliders[name].focus_set()

    # What the function does:
    #     Adjusts only the hovered attribute by its declared slider step.
    # Inputs (if any):
    #     name: hovered setting; event: mouse wheel event.
    # Outputs (if any):
    #     'break'; disabled controls never change or scroll accidentally.
    def wheel(self, name, event):
        slider = self.sliders[name]
        if str(slider.cget('state')) == 'normal' and event.delta:
            steps = max(1, abs(event.delta)//120)*(1 if event.delta > 0 else -1)
            value = self.values[name].get()+steps*float(slider.cget('resolution'))
            self.values[name].set(round(max(float(slider.cget('from')), min(float(slider.cget('to')), value)), 6))
        return 'break'

    # What the function does:
    #     Applies range-pair rules and queues one debounced map regeneration.
    # Inputs (if any):
    #     name: changed setting; args: Tk trace metadata.
    # Outputs (if any):
    #     None; moving a minimum above its maximum raises the maximum, and vice versa.
    def value_changed(self, name, *args):
        if self.loading:
            return
        self.loading = True
        for low, high in (('cluster_min', 'cluster_max'), ('child_min', 'child_max')):
            if self.values[low].get() > self.values[high].get():
                target, source = (high, low) if name == low else (low, high)
                self.values[target].set(self.values[source].get())
        self.loading = False
        self.update_states()
        self.changed()

    # What the function does:
    #     Disables settings that do not apply to the preset or active depth.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; inactive level settings are retained for later reactivation.
    def update_states(self):
        enabled = {'open_end_margin': not PRESETS[self.kind].closed}
        for level in range(1, 4):
            for prefix in ('distance_', 'shrink_'):
                enabled[prefix+str(level)] = level <= int(self.values['levels'].get())
        for name, active in enabled.items():
            self.sliders[name].configure(state='normal' if active else 'disabled')
            self.labels[name].configure(foreground='' if active else '#888888')

    # What the function does:
    #     Captures an immutable preset for use by the background generator.
    # Inputs (if any):
    #     None; reads the current sliders.
    # Outputs (if any):
    #     Validated MapPreset, preserving map-type closure and inversion.
    def snapshot(self):
        v = {name: value.get() for name, value in self.values.items()}
        levels = int(v['levels'])
        preset = replace(PRESETS[self.kind],
            clusters=(int(v['cluster_min']), int(v['cluster_max'])),
            children_per_parent=(int(v['child_min']), int(v['child_max'])),
            distance_ratios=tuple(v[f'distance_{i}'] for i in range(1, levels+1)),
            blob_shrink_ratios=tuple(v[f'shrink_{i}'] for i in range(1, levels+1)),
            **{name: v[name] for name in ('child_angle_variation', 'seed_jitter', 'open_end_margin', 'blob_radius_ratio',
                'blob_size_variation', 'curve_scale', 'curve_waviness')})
        preset.validate()
        return preset

    # What the function does:
    #     Loads preset values without scheduling intermediate generations.
    # Inputs (if any):
    #     preset: settings to display.
    # Outputs (if any):
    #     None.
    def load(self, preset):
        self.loading = True
        values = dict(cluster_min=preset.clusters[0], cluster_max=preset.clusters[1],
                      child_min=preset.children_per_parent[0], child_max=preset.children_per_parent[1],
                      levels=len(preset.distance_ratios))
        for name in ('child_angle_variation', 'seed_jitter', 'open_end_margin', 'blob_radius_ratio', 'blob_size_variation',
                     'curve_scale', 'curve_waviness'):
            values[name] = getattr(preset, name)
        for i in range(1, 4):
            values[f'distance_{i}'] = preset.distance_ratios[min(i-1, len(preset.distance_ratios)-1)]
            values[f'shrink_{i}'] = preset.blob_shrink_ratios[min(i-1, len(preset.blob_shrink_ratios)-1)]
        for name, value in values.items():
            self.values[name].set(value)
        self.loading = False
        self.title.set(self.kind)
        self.update_states()

    # What the function does:
    #     Switches presets while remembering each type's custom settings.
    # Inputs (if any):
    #     kind: destination map type; keep_shape: preserve parameters for land/sea flip.
    # Outputs (if any):
    #     None; caller triggers generation after all controls are ready.
    def select_kind(self, kind, keep_shape=False):
        current = self.snapshot()
        self.saved[self.kind] = current
        self.kind = kind
        self.load(current if keep_shape else self.saved[kind])

    # What the function does:
    #     Restores the selected type's default generation settings.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; regenerates with the existing seed.
    def reset(self):
        self.load(PRESETS[self.kind])
        self.changed()
