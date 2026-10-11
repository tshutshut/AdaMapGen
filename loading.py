"""Small fixed-position loading indicator shared by editing tabs."""
from tkinter import ttk


class LoadingBar(ttk.Frame):
    # What the function does:
    #     Reserves a fixed-height strip so loading never shifts the controls.
    # Inputs (if any):
    #     parent: tab containing the indicator.
    # Outputs (if any):
    #     A hidden indeterminate bar with stable layout space.
    def __init__(self, parent):
        super().__init__(parent, height=12)
        self.pack(fill='x', pady=(0, 10))
        self.pack_propagate(False)
        self.progress = ttk.Progressbar(self, mode='indeterminate')
        self.busy = False

    # What the function does:
    #     Shows and animates the bar only while work is pending or running.
    # Inputs (if any):
    #     busy: whether the corresponding tab has unfinished work.
    # Outputs (if any):
    #     None; repeated calls do not restart the animation.
    def set_busy(self, busy):
        busy = bool(busy)
        if busy == self.busy:
            return
        self.busy = busy
        if busy:
            self.progress.pack(fill='both', expand=True)
            self.progress.start(20)
        else:
            self.progress.stop()
            self.progress.pack_forget()
