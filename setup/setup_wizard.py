# -*- coding: utf-8 -*-
"""本地部署伴生体：首次配置向导。

只写入 public_companion/profile/profile.local.json 和
public_companion/system/config.local.json，不联网、不上传用户数据。
"""
import json
import shutil
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, colorchooser


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "profile" / "profile.local.json"
CONFIG_PATH = ROOT / "system" / "config.local.json"


class SetupWizard:
    def __init__(self, root):
        self.root = root
        self.root.title("本地部署伴生体 · 首次配置")
        self.root.geometry("820x780")
        self.root.minsize(720, 680)
        # 轻杂志风：奶油白、暖灰和低饱和珊瑚色。
        self.bg = "#F7F3EF"
        self.panel = "#FFFCF9"
        self.fg = "#2B2623"
        self.muted = "#8A7D75"
        self.accent = "#D98979"
        self.input_bg = "#F1EAE5"
        self._painted = []
        self.root.configure(bg=self.bg)
        self.vars = {name: tk.StringVar() for name in (
            "name", "owner_name", "relationship", "base_url", "model", "api_base_url", "api_model", "api_key")}
        self.texts = {}
        self._build()
        self._load_existing()

    def _label(self, parent, text):
        return tk.Label(parent, text=text, bg=self.panel, fg=self.fg, anchor="w")

    def _build(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
            style.configure("TNotebook", background=self.bg, borderwidth=0)
            style.configure("TNotebook.Tab", background="#EDE4DE", foreground=self.muted, padding=(18, 9), font=("Microsoft YaHei UI", 10))
            style.map("TNotebook.Tab", background=[("selected", self.panel)], foreground=[("selected", self.fg)])
        except tk.TclError:
            pass
        title = tk.Label(self.root, text="本地部署伴生体", font=("Georgia", 24, "bold"), bg=self.bg, fg=self.fg)
        title.pack(anchor="w", padx=34, pady=(28, 6))
        tk.Label(self.root, text="把你想要的伴生体慢慢写下来，资料只保存到本机。", bg=self.bg, fg=self.muted, font=("Microsoft YaHei UI", 10)).pack(anchor="w", padx=36, pady=(0, 24))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=30, pady=4)
        identity = tk.Frame(notebook, bg=self.panel)
        model = tk.Frame(notebook, bg=self.panel)
        notebook.add(identity, text="角色资料")
        notebook.add(model, text="模型配置")

        self._form_row(identity, 0, "伴生体名字", "name")
        self._form_row(identity, 1, "你的称呼", "owner_name")
        self._form_row(identity, 2, "关系类型", "relationship")
        self._text_row(identity, 3, "身份设定", "identity", "他是谁？来自哪里？平时如何生活？")
        self._text_row(identity, 5, "性格描述", "personality", "他的性格、习惯和价值观")
        self._text_row(identity, 7, "说话方式", "speech_style", "他通常怎样说话？")
        self._text_row(identity, 9, "相处边界", "boundaries", "哪些事情他不会擅自做？")

        self._form_row(model, 0, "本地模型地址", "base_url")
        self._form_row(model, 1, "本地模型名称", "model")
        self._form_row(model, 2, "云端 API 地址（可选）", "api_base_url")
        self._form_row(model, 3, "云端模型名称（可选）", "api_model")
        self._form_row(model, 4, "云端 API key（可选）", "api_key", secret=True)
        tk.Label(model, text="云端 API key 只写入本机配置，不会显示在公开仓库。", bg=self.panel, fg=self.muted, anchor="w").grid(row=5, column=0, columnspan=2, sticky="w", padx=22, pady=16)

        bottom = tk.Frame(self.root, bg=self.bg)
        bottom.pack(fill="x", padx=34, pady=22)
        tk.Button(bottom, text="界面颜色", command=self._choose_color, bg="#EDE4DE", fg=self.fg, relief="flat", padx=14, pady=9).pack(side="left")
        tk.Button(bottom, text="导入角色图片", command=self._import_image, bg="#EDE4DE", fg=self.fg, relief="flat", padx=14, pady=9).pack(side="left", padx=10)
        tk.Button(bottom, text="保存配置", command=self._save, bg=self.accent, fg="white", relief="flat", padx=24, pady=9).pack(side="right")

    def _form_row(self, parent, row, label, key, secret=False):
        self._label(parent, label).grid(row=row, column=0, sticky="w", padx=18, pady=9)
        entry = tk.Entry(parent, textvariable=self.vars[key], width=55, show="•" if secret else "", bg=self.input_bg, fg=self.fg, insertbackground=self.fg, relief="flat")
        entry.grid(row=row, column=1, sticky="ew", padx=18, pady=13, ipady=5)
        parent.grid_columnconfigure(1, weight=1)

    def _text_row(self, parent, row, label, key, hint):
        self._label(parent, label).grid(row=row, column=0, sticky="nw", padx=18, pady=9)
        text = tk.Text(parent, height=6, width=52, bg=self.input_bg, fg=self.fg, insertbackground=self.fg, wrap="word", relief="flat", padx=12, pady=10)
        text.insert("1.0", hint)
        text.grid(row=row, column=1, sticky="ew", padx=12, pady=9)
        self.texts[key] = text
        parent.grid_columnconfigure(1, weight=1)

    def _choose_color(self):
        value = colorchooser.askcolor(color=self.bg, title="选择界面背景色")[1]
        if not value:
            return
        self.bg = value
        self.root.configure(bg=value)
        self._repaint(self.root)

    def _repaint(self, widget):
        try:
            if isinstance(widget, (tk.Frame, tk.LabelFrame)):
                widget.configure(bg=self.bg if widget is self.root else self.panel)
            elif isinstance(widget, tk.Label):
                widget.configure(bg=self.bg if widget.master is self.root else self.panel)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._repaint(child)

    def _load_existing(self):
        try:
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            for key in ("name", "owner_name", "relationship"):
                self.vars[key].set(data.get(key, ""))
            for key in self.texts:
                if data.get(key):
                    self.texts[key].delete("1.0", "end")
                    self.texts[key].insert("1.0", data[key])
        except Exception:
            pass
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            self.bg = data.get("ui", {}).get("background", self.bg)
            self.root.configure(bg=self.bg)
            self._repaint(self.root)
            llm, api = data.get("llm", {}), data.get("api_llm", {})
            for key, value in (("base_url", llm.get("base_url", "")), ("model", llm.get("model", "")), ("api_base_url", api.get("base_url", "")), ("api_model", api.get("model", "")), ("api_key", api.get("api_key", ""))):
                self.vars[key].set(value)
        except Exception:
            pass

    def _import_image(self):
        path = filedialog.askopenfilename(title="选择角色图片", filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.webp"), ("所有文件", "*.*")])
        if not path:
            return
        target_dir = ROOT / "profile" / "assets"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / Path(path).name
        try:
            shutil.copy2(path, target)
            messagebox.showinfo("已导入", f"图片已复制到：\n{target}")
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))

    def _save(self):
        profile = {key: self.vars[key].get().strip() for key in ("name", "owner_name", "relationship")}
        profile.update({key: widget.get("1.0", "end").strip() for key, widget in self.texts.items()})
        profile["appearance"] = {"sprite": "profile/assets/"}
        config = {
            "version": "0.1.0",
            "name": "本地部署伴生体",
            "llm": {"provider": "openai-compatible", "base_url": self.vars["base_url"].get().strip(), "model": self.vars["model"].get().strip(), "temperature": 0.7, "max_tokens": 500, "history_messages": 12},
            "api_llm": {"enabled": bool(self.vars["api_key"].get().strip()), "provider": "openai-compatible", "base_url": self.vars["api_base_url"].get().strip(), "model": self.vars["api_model"].get().strip(), "api_key": self.vars["api_key"].get().strip(), "max_tokens": 300},
            "features": {"desktop_pet": True, "small_world": True, "dreams": True, "memory": True, "phone_bridge": False},
            "ui": {"background": self.bg},
        }
        try:
            PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
            CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            messagebox.showinfo("保存成功", "本地配置已保存。")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))


if __name__ == "__main__":
    root = tk.Tk()
    SetupWizard(root)
    root.mainloop()
