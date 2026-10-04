"""Photo-first Tk workspace. Rendering and editing state live in the controller."""

from pathlib import Path
import tkinter as tk
from tkinter import ttk


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.timer = self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def schedule(self, _event=None):
        self.timer = self.widget.after(500, self.show)

    def show(self):
        self.timer = None
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(
            f"+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 5}"
        )
        tk.Label(
            self.window,
            text=self.text,
            background="#20282c",
            foreground="white",
            font=("Segoe UI", 9),
            padx=8,
            pady=5,
        ).pack()

    def hide(self, _event=None):
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


def configure_style(root):
    from PIL import Image, ImageDraw, ImageTk

    style = ttk.Style(root)
    style.theme_use("clam")
    root.option_add("*Font", ("Segoe UI", 10))
    style.configure("TFrame", background="#ffffff")
    style.configure("App.TFrame", background="#f4f5f6")
    style.configure("Panel.TFrame", background="#ffffff")
    for name, color, size in (
        ("Panel", "#242a30", 10),
        ("Muted", "#687278", 9),
        ("Warning", "#a44225", 9),
        ("Heading", "#242a30", 10),
    ):
        style.configure(
            f"{name}.TLabel",
            background="white",
            foreground=color,
            font=("Segoe UI", size, "bold")
            if name == "Heading"
            else ("Segoe UI", size),
        )
    style.configure(
        "TButton",
        padding=(12, 7),
        background="#ffffff",
        foreground="#30363b",
        borderwidth=0,
        relief="flat",
        font=("Segoe UI", 10),
    )
    style.map(
        "TButton",
        background=[("active", "#edf0f2"), ("pressed", "#dce3e7")],
        foreground=[("disabled", "#a0a7ac")],
    )
    style.configure(
        "Primary.TButton",
        background="#176f65",
        foreground="white",
        font=("Segoe UI", 10, "bold"),
    )
    style.map(
        "Primary.TButton",
        background=[("active", "#105b53"), ("disabled", "#c7d8d4")],
        foreground=[("disabled", "#7a928c")],
    )
    style.configure("Icon.TButton", padding=8, width=3)
    style.configure(
        "TNotebook", background="white", borderwidth=0, tabmargins=(8, 4, 8, 0)
    )
    style.configure(
        "TNotebook.Tab", padding=(15, 9), background="#f0f2f3", borderwidth=0
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", "white")],
        foreground=[("selected", "#156b62")],
    )
    style.configure(
        "Horizontal.TScale",
        background="white",
        troughcolor="#e5e9eb",
        borderwidth=0,
        sliderlength=16,
    )
    track = Image.new("RGBA", (24, 24), "white")
    ImageDraw.Draw(track).rounded_rectangle((0, 10, 23, 13), radius=2, fill="#dfe5e8")
    thumb = Image.new("RGBA", (18, 24), (0, 0, 0, 0))
    ImageDraw.Draw(thumb).ellipse(
        (1, 4, 16, 19), fill="white", outline="#687e79", width=2
    )
    root.scale_images = [ImageTk.PhotoImage(track), ImageTk.PhotoImage(thumb)]
    style.element_create(
        "OpenRAW.trough",
        "image",
        root.scale_images[0],
        border=(4, 0, 4, 0),
        sticky="we",
    )
    style.element_create("OpenRAW.slider", "image", root.scale_images[1])
    style.layout(
        "Horizontal.TScale",
        [
            (
                "OpenRAW.trough",
                {
                    "sticky": "we",
                    "children": [("OpenRAW.slider", {"side": "left", "sticky": ""})],
                },
            )
        ],
    )
    style.configure(
        "TNotebook", bordercolor="white", lightcolor="white", darkcolor="white"
    )
    style.configure(
        "TNotebook.Tab", bordercolor="white", lightcolor="white", darkcolor="white"
    )
    style.configure(
        "Processing.Horizontal.TProgressbar",
        background="#176f65",
        troughcolor="#e5e9eb",
        thickness=3,
    )
    style.configure("TCheckbutton", background="white")
    style.configure("TRadiobutton", background="white")
    style.configure(
        "Vertical.TScrollbar",
        background="#dce2e5",
        troughcolor="white",
        borderwidth=0,
        arrowsize=10,
    )


def build_workspace(app, filedialog, messagebox):
    app.filedialog, app.messagebox = filedialog, messagebox
    app.icons = {}
    app.edit_scales = []
    root = app.root
    root.configure(background="#ffffff")
    root.columnconfigure(0, weight=1)
    root.columnconfigure(1, weight=0, minsize=292)
    root.rowconfigure(1, weight=1)
    toolbar = ttk.Frame(root, padding=(14, 9))
    toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
    toolbar.columnconfigure(5, weight=1)
    ttk.Label(
        toolbar,
        text="OpenRAW Studio",
        style="Heading.TLabel",
        font=("Segoe UI", 14, "bold"),
    ).grid(row=0, column=0, padx=(0, 20))

    def button(
        parent, icon, title, command, *, text="", style="Icon.TButton", disabled=False
    ):
        if icon not in app.icons:
            app.icons[icon] = tk.PhotoImage(
                file=str(Path(__file__).parent / "icons" / f"{icon}.png")
            )
        result = ttk.Button(
            parent,
            image=app.icons[icon],
            text=text,
            compound="left",
            style=style,
            command=command,
            state="disabled" if disabled else "normal",
        )
        Tooltip(result, title)
        return result

    app.import_button = button(
        toolbar, "folder-open", "Import RAW photo", app._choose_source
    )
    app.import_button.grid(row=0, column=1)
    app.import_folder_button = button(
        toolbar, "folder", "Import folder", app._choose_library_folder
    )
    app.import_folder_button.grid(row=0, column=2, padx=(2, 12))
    app.undo_button = button(toolbar, "undo-2", "Undo", app._undo, disabled=True)
    app.undo_button.grid(row=0, column=3)
    app.redo_button = button(toolbar, "redo-2", "Redo", app._redo, disabled=True)
    app.redo_button.grid(row=0, column=4)
    app.auto_adjust_button = ttk.Button(
        toolbar, text="Auto", command=app._auto_adjust, state="disabled"
    )
    app.auto_adjust_button.grid(row=0, column=6, padx=8)
    app.process_button = ttk.Button(
        toolbar,
        text="Export JPEG",
        style="Primary.TButton",
        command=app._export_photo,
        state="disabled",
    )
    app.process_button.grid(row=0, column=7)

    viewer = ttk.Frame(root, style="App.TFrame")
    viewer.grid(row=1, column=0, sticky="nsew")
    viewer.columnconfigure(0, weight=1)
    viewer.rowconfigure(1, weight=1)
    photo_bar = ttk.Frame(viewer, padding=(14, 6))
    photo_bar.grid(row=0, column=0, sticky="ew")
    photo_bar.columnconfigure(0, weight=1)
    source_label = ttk.Label(
        photo_bar, textvariable=app.source_var, style="Panel.TLabel", width=1
    )
    source_label.grid(row=0, column=0, sticky="ew")
    app.compare_button = button(
        photo_bar,
        "columns-2",
        "Compare original RAW and edited photo",
        app._toggle_compare,
        disabled=True,
    )
    ttk.Label(photo_bar, textvariable=app.view_var, style="Muted.TLabel", width=7).grid(
        row=0, column=1, padx=(6, 4)
    )
    app.compare_button.grid(row=0, column=2, padx=(0, 4))
    app.zoom_combo = ttk.Combobox(
        photo_bar,
        values=("Fit", "2x", "4x", "100%", "200%"),
        textvariable=app.zoom_var,
        state="readonly",
        width=5,
    )
    app.zoom_combo.grid(row=0, column=3)
    app.zoom_combo.bind("<<ComboboxSelected>>", app._zoom_changed)
    app.preview_label = tk.Label(
        viewer,
        background="#e7ebee",
        foreground="#737e85",
        text="No photo selected",
        font=("Segoe UI", 16),
        width=1,
        height=1,
        borderwidth=0,
    )
    app.preview_label.grid(row=1, column=0, sticky="nsew")
    app.preview_label.bind("<Configure>", app._queue_preview_resize)
    app.preview_label.bind("<ButtonPress-1>", app._pan_start)
    app.preview_label.bind("<B1-Motion>", app._pan_move)
    app.preview_label.bind("<Double-Button-1>", app._zoom_toggle)
    footer = ttk.Frame(viewer, padding=(12, 8))
    footer.grid(row=2, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    ttk.Label(footer, textvariable=app.status_var, style="Muted.TLabel", width=1).grid(
        row=0, column=0, sticky="ew"
    )
    ttk.Label(footer, textvariable=app.edit_status_var, style="Muted.TLabel").grid(
        row=0, column=1, padx=(8, 0)
    )
    ttk.Label(
        footer, textvariable=app.preview_state_var, style="Muted.TLabel", width=1
    ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
    app.progress_bar = ttk.Progressbar(
        footer, mode="determinate", style="Processing.Horizontal.TProgressbar"
    )
    app.progress_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
    footer.rowconfigure(2, minsize=app.progress_bar.winfo_reqheight() + 6)
    app.progress_bar.grid_remove()

    inspector = ttk.Frame(root, width=292)
    inspector.grid(row=1, column=1, sticky="nsew")
    inspector.grid_propagate(False)
    inspector.rowconfigure(0, weight=1)
    inspector.columnconfigure(0, weight=1)
    app.inspector_tabs = ttk.Notebook(inspector)
    app.inspector_tabs.grid(row=0, column=0, sticky="nsew")
    adjust_host = ttk.Frame(app.inspector_tabs)
    library = ttk.Frame(app.inspector_tabs, padding=16)
    export_host = ttk.Frame(app.inspector_tabs)
    app.inspector_tabs.add(adjust_host, text="Adjust")
    app.inspector_tabs.add(library, text="Library")
    app.inspector_tabs.add(export_host, text="Export")
    info = ttk.Frame(app.inspector_tabs, padding=16)
    app.inspector_tabs.add(info, text="Info")
    ttk.Label(
        info, textvariable=app.photo_info_var, style="Muted.TLabel", wraplength=244
    ).pack(anchor="nw")
    adjust_host.rowconfigure(0, weight=1)
    adjust_host.columnconfigure(0, weight=1)
    app.controls_canvas = tk.Canvas(
        adjust_host, width=270, highlightthickness=0, background="white"
    )
    scroll = ttk.Scrollbar(adjust_host, command=app.controls_canvas.yview)
    app.controls_canvas.configure(yscrollcommand=scroll.set)
    app.controls_canvas.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    controls = ttk.Frame(app.controls_canvas, padding=(16, 14))
    window = app.controls_canvas.create_window(0, 0, window=controls, anchor="nw")
    controls.bind(
        "<Configure>",
        lambda _e: app.controls_canvas.configure(
            scrollregion=app.controls_canvas.bbox("all")
        ),
    )
    app.controls_canvas.bind(
        "<Configure>",
        lambda e: app.controls_canvas.itemconfigure(window, width=e.width),
    )
    export_host.rowconfigure(0, weight=1)
    export_host.columnconfigure(0, weight=1)
    app.export_canvas = tk.Canvas(
        export_host, width=270, highlightthickness=0, background="white"
    )
    export_scroll = ttk.Scrollbar(export_host, command=app.export_canvas.yview)
    app.export_canvas.configure(yscrollcommand=export_scroll.set)
    app.export_canvas.grid(row=0, column=0, sticky="nsew")
    export_scroll.grid(row=0, column=1, sticky="ns")
    export = ttk.Frame(app.export_canvas, padding=16)
    export_window = app.export_canvas.create_window(0, 0, window=export, anchor="nw")
    export.bind(
        "<Configure>",
        lambda _e: app.export_canvas.configure(
            scrollregion=app.export_canvas.bbox("all")
        ),
    )
    app.export_canvas.bind(
        "<Configure>",
        lambda e: app.export_canvas.itemconfigure(export_window, width=e.width),
    )
    root.bind_all("<MouseWheel>", app._scroll_controls, add="+")
    app.support_notice_label = ttk.Label(
        controls, textvariable=app.support_notice_var,
        style="Warning.TLabel", wraplength=238,
    )
    app.histogram_canvas = tk.Canvas(
        controls, height=76, width=240, background="#f7f8f9", highlightthickness=0
    )
    app.histogram_canvas.pack(fill="x")
    app.histogram_canvas.bind("<Configure>", app._resize_histogram)
    app.histogram_status_label = ttk.Label(
        controls,
        textvariable=app.histogram_status_var,
        style="Muted.TLabel",
        wraplength=238,
    )
    app.histogram_status_label.pack(anchor="w", pady=(6, 12))
    amount_row = ttk.Frame(controls)
    amount_row.pack(fill="x")
    ttk.Label(amount_row, text="Global Auto strength", style="Panel.TLabel").pack(side="left")
    ttk.Label(
        amount_row, textvariable=app.auto_strength_label_var, style="Muted.TLabel"
    ).pack(side="right")
    app.auto_strength_scale = ttk.Scale(
        controls,
        from_=0,
        to=100,
        variable=app.auto_strength_var,
        command=app._change_auto_strength,
    )
    app.auto_strength_scale.pack(fill="x", pady=(4, 10))
    app.auto_summary_label = ttk.Label(
        controls,
        textvariable=app.auto_summary_var,
        style="Muted.TLabel",
        wraplength=238,
    )
    app.auto_summary_label.pack(anchor="w", pady=(0, 8))
    app.person_mask_button = ttk.Checkbutton(
        controls, text="Person mask", variable=app.person_mask_var,
        command=app._fit_live_image, state="disabled",
    )
    app.person_mask_button.pack(anchor="w", pady=(0, 8))
    Tooltip(app.person_mask_button, "Inspect the saved subject selection, or Auto's person area; not a skin or face mask")

    def section(name, expanded=True):
        ttk.Separator(controls).pack(fill="x", pady=(4, 10))
        state = tk.BooleanVar(value=expanded)
        content = ttk.Frame(controls)

        def toggle():
            if state.get():
                content.pack(fill="x", after=header)
            else:
                content.pack_forget()

        header = ttk.Checkbutton(controls, text=name, variable=state, command=toggle)
        header.pack(fill="x", pady=(0, 7))
        toggle()
        return content

    light = section("Light")
    subject = section("Subject")
    subject_row = ttk.Frame(subject)
    subject_row.pack(fill="x", pady=(0, 7))
    app.subject_enabled_button = ttk.Checkbutton(
        subject_row, text="Enabled", variable=app.subject_enabled_var,
        command=app._subject_changed, state="disabled",
    )
    app.subject_enabled_button.pack(side="left")
    app.subject_select_button = button(
        subject_row, "wand-sparkles", "Select person", app._select_subject,
        text="  Select", style="TButton", disabled=True,
    )
    app.subject_select_button.configure(width=0)
    app.subject_select_button.pack(side="right")
    app.subject_auto_button = button(
        subject_row, "wand-sparkles", "Auto subject exposure: meter face and surrounding light",
        app._auto_subject, disabled=True,
    )
    app.subject_auto_button.pack(side="right", padx=(0, 5))
    row = ttk.Frame(subject)
    row.pack(fill="x")
    ttk.Label(row, text="Exposure", style="Panel.TLabel").pack(side="left")
    ttk.Label(row, textvariable=app.subject_exposure_label_var, style="Muted.TLabel").pack(side="right")
    app.subject_exposure_scale = ttk.Scale(
        subject, from_=-1, to=1, variable=app.subject_exposure_var,
        command=app._subject_changed, state="disabled",
    )
    app.subject_exposure_scale.pack(fill="x", pady=(4, 11))
    app.subject_exposure_scale.bind("<ButtonRelease-1>", lambda _e: app._commit_edit())
    app.subject_exposure_scale.bind("<Double-Button-1>", lambda _e: app._reset_one("subject_exposure"))
    Tooltip(app.subject_exposure_scale, "Local rendered-image exposure; preserves white, cannot recover clipped RAW detail")
    color = section("Color", expanded=False)
    detail = section("Detail", expanded=False)
    for parent, key, title, minimum, maximum in (
        (light, "exposure", "Exposure", -2, 2),
        (light, "contrast", "Contrast", -1, 1),
        (light, "highlights", "Highlights", -1, 1),
        (light, "shadows", "Shadows", -1, 1),
        (color, "warmth", "Temperature", -1, 1),
        (color, "tint", "Tint", -1, 1),
        (color, "saturation", "Saturation", -1, 1),
        (detail, "color_noise", "Color noise", 0, 1),
        (detail, "luminance_noise", "Luminance noise", 0, 1),
    ):
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Label(row, text=title, style="Panel.TLabel").pack(side="left")
        if key == "color_noise":
            app.auto_color_noise_button = button(
                row, "wand-sparkles", "Auto color noise", app._auto_color_noise,
                disabled=True,
            )
            app.auto_color_noise_button.pack(side="right", padx=(4, 0))
        ttk.Label(
            row, textvariable=getattr(app, key + "_label_var"), style="Muted.TLabel"
        ).pack(side="right")
        scale = ttk.Scale(
            parent,
            from_=minimum,
            to=maximum,
            variable=getattr(app, key + "_var"),
            command=app._sync_adjustment_labels,
        )
        scale.pack(fill="x", pady=(4, 11))
        scale.bind("<ButtonRelease-1>", lambda _e: app._commit_edit())
        scale.bind("<Double-Button-1>", lambda _e, name=key: app._reset_one(name))
        app.edit_scales.append(scale)
    button(
        controls,
        "rotate-ccw",
        "Reset adjustments",
        app._reset_adjustments,
        text="  Reset",
        style="TButton",
    ).pack(anchor="w", pady=(8, 0))

    ttk.Label(library, text="Photos", style="Heading.TLabel").pack(anchor="w")
    ttk.Label(
        library,
        textvariable=app.library_status_var,
        style="Muted.TLabel",
        wraplength=244,
    ).pack(anchor="w", pady=(6, 12))
    app.library_listbox = tk.Listbox(
        library,
        height=8,
        borderwidth=0,
        highlightthickness=1,
        highlightbackground="#e0e5e7",
        selectbackground="#dbeee9",
        selectforeground="#174e45",
        activestyle="none",
        exportselection=False,
        font=("Segoe UI", 10),
    )
    app.library_listbox.pack(fill="both", expand=True)
    app.library_listbox.bind("<<ListboxSelect>>", app._select_library_item)
    ttk.Button(library, text="Import folder", command=app._choose_library_folder).pack(
        fill="x", pady=(12, 0)
    )
    demo = ttk.Menubutton(library, text="Sample photos")
    menu = tk.Menu(demo, tearoff=False)
    menu.add_command(label="Create sample DNG", command=app._create_sample_source)
    menu.add_command(label="Create sample NEF", command=app._create_sample_nikon_source)
    demo.configure(menu=menu)
    demo.pack(fill="x", pady=(8, 0))

    ttk.Label(export, text="Output", style="Heading.TLabel").pack(anchor="w")
    ttk.Label(
        export, textvariable=app.output_var, style="Muted.TLabel", wraplength=244
    ).pack(anchor="w", pady=(8, 10))
    ttk.Button(export, text="Choose folder", command=app._choose_output).pack(fill="x")
    ttk.Label(export, text="Format", style="Panel.TLabel").pack(
        anchor="w", pady=(20, 6)
    )
    app.export_format_combo = ttk.Combobox(
        export,
        textvariable=app.export_format_var,
        values=("JPEG", "TIFF"),
        state="readonly",
    )
    app.export_format_combo.pack(fill="x")
    app.export_format_combo.bind("<<ComboboxSelected>>", app._sync_export_options)
    ttk.Label(export, text="Bit depth", style="Panel.TLabel").pack(
        anchor="w", pady=(16, 6)
    )
    app.export_bit_depth_combo = ttk.Combobox(
        export,
        textvariable=app.export_bit_depth_var,
        values=(8, 16),
        state="disabled",
    )
    app.export_bit_depth_combo.pack(fill="x")
    app.export_bit_depth_combo.bind("<<ComboboxSelected>>", app._sync_export_options)
    row = ttk.Frame(export)
    row.pack(fill="x", pady=(16, 4))
    ttk.Label(row, text="JPEG quality", style="Panel.TLabel").pack(side="left")
    ttk.Label(row, textvariable=app.jpeg_quality_label_var, style="Muted.TLabel").pack(
        side="right"
    )
    app.jpeg_quality_scale = ttk.Scale(
        export,
        from_=60,
        to=100,
        variable=app.jpeg_quality_var,
        command=app._sync_export_options,
    )
    app.jpeg_quality_scale.pack(fill="x")
    ttk.Label(export, text="Folder processing", style="Panel.TLabel").pack(
        anchor="w", pady=(16, 6)
    )
    app.batch_mode_combo = ttk.Combobox(
        export,
        textvariable=app.batch_mode_var,
        values=("Current adjustments", "Saved edits", "Auto each photo"),
        state="readonly",
    )
    app.batch_mode_combo.pack(fill="x")
    Tooltip(app.batch_mode_combo, "Current adjustments copies global controls only; Saved edits retains each photo's own subject layer")
    app.batch_button = ttk.Button(
        export, text="Export folder", command=app._export_folder, state="disabled"
    )
    app.batch_button.pack(fill="x", pady=(16, 0))
    app.cancel_batch_button = ttk.Button(
        export, text="Stop batch", command=app._cancel_batch, state="disabled"
    )
    app.cancel_batch_button.pack(fill="x", pady=(4, 0))
    app.export_label = ttk.Label(export, text="", style="Muted.TLabel", wraplength=240)
    app.export_label.pack(anchor="w", pady=12)
    app.open_export_button = ttk.Button(
        export, text="Open export", command=app._open_export, state="disabled"
    )
    app.open_export_button.pack(fill="x")
    app.open_folder_button = ttk.Button(
        export,
        text="Open output folder",
        command=app._open_output_folder,
        state="disabled",
    )
    app.open_folder_button.pack(fill="x", pady=(6, 0))
    # This internal action remains available for preview-only formats/retries.
    app.preview_button = ttk.Button(export, command=app._update_preview)
    app._sync_export_options(update_status=False)
    app._set_busy(False)
