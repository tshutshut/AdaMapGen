"""Transactional contour previews: baseline terrain is untouched until Apply."""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import tkinter as tk
from tkinter import ttk
import numpy as np
from contours import vary_contours
from loading import LoadingBar


class ContourPanel(ttk.Frame):
    # What the function does:
    #     Builds selection, live noise controls, and apply/cancel actions.
    # Inputs (if any):
    #     parent: notebook; app: owning application.
    # Outputs (if any):
    #     A contour tab with its own background preview worker.
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.loading = LoadingBar(self)
        self.app = app
        self.selected = set()
        self.preview_land = None
        self.revision = 0
        self.future = self.poll_id = None
        self.pending = None
        self.requested = False
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.all_objects = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text='Apply to all objects', variable=self.all_objects,
                        command=partial(self.selection_changed, True)).pack(anchor='w')
        self.selection_text = tk.StringVar(value='All objects')
        ttk.Label(self, textvariable=self.selection_text, wraplength=390).pack(anchor='w', pady=8)
        ttk.Label(self, text='Alt+click toggles objects in the selection.', wraplength=390).pack(anchor='w')
        self.values = []
        for label, low, high, step, default in (
                ('Displacement (% of shorter side)', 0, 5, .1, 1),
                ('Variation scale (% of shorter side)', .5, 50, .5, 10),
                ('Detail levels', 1, 6, 1, 3),
                ('Roughness', 0, 1, .05, .5),
                ('Variation seed', 0, 99999, 1, 1)):
            ttk.Label(self, text=f'{label} [{low:g}–{high:g}]').pack(anchor='w', pady=(8, 0))
            value = tk.DoubleVar(value=default)
            slider = tk.Scale(self, from_=low, to=high, resolution=step, variable=value,
                              orient='horizontal', length=380)
            slider.pack(fill='x')
            slider.bind('<MouseWheel>', partial(self.wheel, slider, value))
            value.trace_add('write', self.settings_changed)
            self.values.append(value)
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=12)
        self.apply_button = ttk.Button(actions, text='Apply', command=self.apply, state='disabled')
        self.apply_button.pack(side='left', expand=True, fill='x')
        ttk.Button(actions, text='Cancel', command=self.cancel).pack(side='left', expand=True, fill='x')
        self.message = tk.StringVar(value='Adjust sliders to update the contours.')
        ttk.Label(self, textvariable=self.message, wraplength=390).pack(anchor='w')

    # What the function does:
    #     Adjusts the hovered slider by one increment without affecting the brush.
    # Inputs (if any):
    #     slider/value: target control; event: wheel event.
    # Outputs (if any):
    #     'break'.
    def wheel(self, slider, value, event):
        if event.delta:
            change = float(slider.cget('resolution'))*(1 if event.delta > 0 else -1)
            value.set(round(np.clip(value.get()+change, float(slider.cget('from')), float(slider.cget('to'))), 6))
        return 'break'

    # What the function does:
    #     Coalesces slider movement and queues the latest contour calculation.
    # Inputs (if any):
    #     args: Tk variable trace data.
    # Outputs (if any):
    #     None; the next preview always starts from committed terrain.
    def settings_changed(self, *args):
        self.revision += 1
        self.requested = True
        self.apply_button.state(['disabled'])
        if self.pending is not None:
            self.after_cancel(self.pending)
        # Keep the last visible result until its replacement is ready.
        self.pending = self.after(150, self.preview)
        self.message.set('Updating contours…')

    # What the function does:
    #     Toggles an object in a collection and switches off all-object targeting.
    # Inputs (if any):
    #     identity: clicked region ID.
    # Outputs (if any):
    #     None; updates selection highlights.
    def toggle(self, identity):
        self.all_objects.set(False)
        self.selected.symmetric_difference_update({identity})
        self.selection_changed(live=True)

    # What the function does:
    #     Updates the selection caption and discards obsolete previews.
    # Inputs (if any):
    #     live: regenerate for a user selection change; otherwise just invalidate.
    # Outputs (if any):
    #     None.
    def selection_changed(self, live=False):
        self.selection_text.set('All objects' if self.all_objects.get() else
                                f'{len(self.selected)} selected: '+', '.join(map(str, sorted(self.selected))))
        self.cancel()
        if live:
            self.settings_changed()

    # What the function does:
    #     Starts a preview on a frozen baseline without modifying edit history.
    # Inputs (if any):
    #     None; reads selected IDs and current sliders.
    # Outputs (if any):
    #     None; reports unavailable or stale object analysis instead of guessing.
    def preview(self):
        if self.pending is not None:
            self.after_cancel(self.pending)
            self.pending = None
        if self.future is not None:
            self.requested = True
            return
        self.requested = False
        if not self.app.region_current or self.app.future is not None or self.app.pending is not None:
            self.message.set('Wait for map and object recalculation to finish.')
            return
        if not self.all_objects.get() and not self.selected:
            self.message.set('Alt+click one or more objects, or choose all objects.')
            return
        self.active = self.revision
        self.map_revision = self.app.region_revision
        strength, scale, detail, roughness, seed = [v.get() for v in self.values]
        selected = None if self.all_objects.get() else set(self.selected)
        self.future = self.executor.submit(vary_contours, self.app.land.copy(), self.app.region_map.labels,
                                           selected, strength, scale, int(detail), roughness, int(seed))
        self.message.set('Updating contours…')
        self.poll_id = self.after(40, self.poll)

    # What the function does:
    #     Displays only a preview matching the current settings and committed map.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; all Tk updates stay on the UI thread.
    def poll(self):
        self.poll_id = None
        if not self.future.done():
            self.poll_id = self.after(40, self.poll)
            return
        future, self.future = self.future, None
        try:
            result = future.result()
            if self.active == self.revision and self.map_revision == self.app.region_revision:
                self.preview_land = result
                self.apply_button.state(['!disabled'])
                self.message.set('Contours updated. Apply to keep them, or Cancel.')
                self.app.draw()
        except Exception as exc:
            if self.active == self.revision:
                self.message.set(f'Contour update failed: {exc}')
        if self.requested and self.pending is None:
            self.preview()

    # What the function does:
    #     Commits a preview as one undoable edit and rebuilds affected objects.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; selection clears because topology and region IDs may change.
    def apply(self):
        if (self.preview_land is None or self.map_revision != self.app.region_revision or
                self.future is not None or self.pending is not None or self.requested):
            return
        result = self.preview_land
        changed = result != self.app.land
        if changed.any():
            self.app.remember_edit()
            for value in (False, True):
                mask = changed & (result == value)
                if mask.any():
                    self.app.strokes.append({'mask': np.packbits(mask), 'shape': mask.shape, 'value': value})
            self.app.land = result
            self.app.invalidate_regions()
            self.app.request_regions()
        self.selected.clear()
        self.selection_changed()
        self.message.set('Applied. Objects are recalculating.' if changed.any() else 'No contour changes at these settings.')

    # What the function does:
    #     Discards transient output without changing the map or undo history.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None; running workers may finish but their results become obsolete.
    def cancel(self):
        self.revision += 1
        self.requested = False
        if self.pending is not None:
            self.after_cancel(self.pending)
            self.pending = None
        self.preview_land = None
        self.apply_button.state(['disabled'])
        self.message.set('Adjust sliders to update the contours.')
        if hasattr(self.app, 'land'):
            self.app.draw()

    # What the function does:
    #     Stops polling and releases the contour worker when the app closes.
    # Inputs (if any):
    #     None.
    # Outputs (if any):
    #     None.
    def close(self):
        if self.pending is not None:
            self.after_cancel(self.pending)
        if self.poll_id is not None:
            self.after_cancel(self.poll_id)
        self.executor.shutdown(wait=False, cancel_futures=True)
