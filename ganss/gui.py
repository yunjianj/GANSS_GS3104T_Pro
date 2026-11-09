"""GTK 3 desktop interface for the GS3104T PRO."""
import copy
import threading
import time
from importlib.resources import files

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib, Gdk, GObject, Pango
except (ImportError, ValueError) as exc:
    raise SystemExit('需要安装 GTK 3 和 Python GObject（python-gobject / python3-gi）。') from exc

from .api import dispatch
from .light_modes import LIGHT_MODES
from .light_preview import preview_color
from .physical_keys import index_from_hardware_keycode
from .gui_state import set_active_silently
from . import profiles
from .protocol import BY_INDEX, parse_profile, resolve_key


class Dropdown(Gtk.MenuButton):
    """A bounded, scrollable dropdown independent of GtkComboBox menus."""
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, position=Gtk.PositionType.BOTTOM):
        super().__init__()
        self.items = []
        self.active_id = None
        self.caption = Gtk.Label(label='请选择')
        self.caption.set_xalign(0)
        self.caption.set_ellipsize(3)
        contents = Gtk.Box(spacing=8)
        contents.pack_start(self.caption, True, True, 0)
        arrow = Gtk.Image.new_from_icon_name('pan-down-symbolic', Gtk.IconSize.MENU)
        contents.pack_end(arrow, False, False, 0)
        self.add(contents)
        self.popover = Gtk.Popover.new(self)
        self.popover.set_position(position)
        self.popover.set_constrain_to(Gtk.PopoverConstraint.NONE)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect('row-activated', self._activate)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_width(240)
        scroll.set_min_content_height(100)
        scroll.set_max_content_height(220)
        scroll.set_propagate_natural_height(True)
        scroll.add(self.listbox)
        self.popover.add(scroll)
        scroll.show_all()
        self.set_popover(self.popover)

    def append(self, item_id, label):
        self.items.append((item_id, label))
        row = Gtk.ListBoxRow()
        text = Gtk.Label(label=label)
        text.set_xalign(0)
        text.set_margin_start(12)
        text.set_margin_end(12)
        text.set_margin_top(7)
        text.set_margin_bottom(7)
        row.add(text)
        self.listbox.add(row)
        row.show_all()

    def append_text(self, label):
        self.append(str(len(self.items)), label)

    def remove_all(self):
        for row in self.listbox.get_children():
            self.listbox.remove(row)
        self.items = []
        self.active_id = None
        self.caption.set_text('请选择')

    def set_active_id(self, item_id):
        for index, (value, label) in enumerate(self.items):
            if value == item_id:
                changed = self.active_id != value
                self.active_id = value
                self.caption.set_text(label)
                self.listbox.select_row(self.listbox.get_row_at_index(index))
                if changed:
                    self.emit('changed')
                return True
        return False

    def get_active_id(self):
        return self.active_id

    def set_active(self, index):
        if 0 <= index < len(self.items):
            self.set_active_id(self.items[index][0])

    def get_active(self):
        return next((i for i, (value, _) in enumerate(self.items)
                     if value == self.active_id), -1)

    def _activate(self, _box, row):
        self.set_active(row.get_index())
        self.popover.popdown()


class KeyboardView(Gtk.ScrolledWindow):
    """Vendor layout diagram with native clickable key controls."""
    __gsignals__ = {'key-selected': (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, preview=False):
        super().__init__()
        self.preview = preview
        self.preview_events = []
        self.preview_mode = None
        self.selected = None
        self.buttons = {}
        self.button_handlers = {}
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.set_min_content_height(280)
        overlay = Gtk.Overlay()
        overlay.set_size_request(800, 280)
        overlay.set_halign(Gtk.Align.CENTER)
        overlay.set_valign(Gtk.Align.START)
        overlay.add(Gtk.Image.new_from_file(str(files('ganss').joinpath('data/keyboard.png'))))
        surface = Gtk.Fixed()
        surface.set_size_request(800, 280)
        overlay.add_overlay(surface)
        self.add(overlay)
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            button.keyboard-key { background: #000000; background-image: none;
                border: 2px solid transparent; border-radius: 3px; box-shadow: none;
                padding: 0; min-width: 0; min-height: 0; color: #f3f3f3; font-size: 9px; }
            button.keyboard-key:hover { background: #1d364d; border-color: #62b5ff; }
            button.keyboard-key:checked { background: #1b4773; border-color: #52b7ff; }
            button.keyboard-key.mapped { border-color: #ffc75b; }
            button.keyboard-key.mapped:checked { border-color: #52b7ff; }
        """)
        self.preview_css = Gtk.CssProvider() if preview else None
        for index, key in sorted(BY_INDEX.items()):
            button = Gtk.ToggleButton()
            labels = {'Backspace': 'Back', 'CapsLock': 'Caps', 'Shift_L': 'Shift',
                      'Shift_R': 'Shift', 'Ctrl_L': 'Ctrl', 'Ctrl_R': 'Ctrl',
                      'Win_L': 'Win', 'Alt_L': 'Alt', 'Alt_R': 'Alt',
                      'PRINT': 'Prt', 'SCROOL': 'Scr', 'pause': 'Pause',
                      'BTPAIR': 'BT', 'LEDOFF': '灯', 'GAME': 'Win',
                      'CALC': '计算', 'VOLDEC': '−', 'MUTE': '●',
                      'VOLINC': '+', 'LOCK': '锁', 'Insert': 'Ins'}
            label = Gtk.Label(label=labels.get(key['name'], key['name']))
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_single_line_mode(True)
            button.add(label)
            button.set_can_focus(not preview)
            if preview:
                button.set_name('light-key-' + str(index))
                button.get_style_context().add_provider(self.preview_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            button.set_tooltip_text('{} (index:{})'.format(key['name'], index))
            button.get_accessible().set_name(key['name'])
            context = button.get_style_context()
            context.add_class('keyboard-key')
            context.add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            x = int(key['rect_left'])
            y = int(key['rect_top'])
            width = int(key['rect_width'])
            height = int(key['rect_height'])
            button.set_size_request(width, height)
            if not preview:
                handler = button.connect('clicked', self._clicked, str(index))
            else:
                handler = button.connect('clicked', self._preview_clicked, index)
            self.button_handlers[str(index)] = handler
            surface.put(button, x, y)
            self.buttons[str(index)] = button

    def _preview_clicked(self, button, index):
        set_active_silently(button, self.button_handlers[str(index)], False)
        self.trigger_preview(index)

    def trigger_preview(self, index):
        key = BY_INDEX[index]
        self.preview_events.append((index, int(key['rect_left']), int(key['rect_top']), time.monotonic()))
        self.preview_events = self.preview_events[-32:]

    def update_lighting(self, mode, rgb, brightness, speed, colorful, elapsed):
        if mode != self.preview_mode:
            self.preview_events.clear()
            self.preview_mode = mode
        now = time.monotonic()
        self.preview_events = [event for event in self.preview_events if now - event[3] < 2]
        events = [(index, x, y, now - timestamp) for index, x, y, timestamp in self.preview_events]
        rules = []
        for index, key in BY_INDEX.items():
            r, g, b = preview_color(mode, rgb, brightness, speed, colorful,
                                    int(key['rect_left']), int(key['rect_top']), index, elapsed, events)
            rules.append('#light-key-{} {{ background-image: none; background-color: rgb({},{},{}); }}'.format(index, r, g, b))
        self.preview_css.load_from_data(('\n'.join(rules)).encode('ascii'))

    def _clicked(self, _button, index):
        self.set_active_id(index)
        self.emit('key-selected', index)

    def set_active_id(self, index):
        if index not in self.buttons:
            return False
        self.selected = index
        for key, button in self.buttons.items():
            set_active_silently(button, self.button_handlers[key], key == index)
        return True

    def get_active_id(self):
        return self.selected

    def clear_selection(self):
        self.selected = None
        for key, button in self.buttons.items():
            set_active_silently(button, self.button_handlers[key], False)

    def set_mapped(self, indices):
        for key, button in self.buttons.items():
            context = button.get_style_context()
            if key in indices:
                context.add_class('mapped')
            else:
                context.remove_class('mapped')


class Window(Gtk.Window):
    def __init__(self):
        super().__init__(title='GANSS GS3104T PRO')
        self.set_default_size(1000, 850)
        self.set_border_width(20)
        self.devices = []
        self.busy = False
        self.updating = False
        self.connect('delete-event', self.on_close)
        self.connect('key-press-event', self.preview_key_press)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add(root)
        title = Gtk.Label()
        title.set_markup('<span size="x-large" weight="bold">GS3104T PRO</span>  键盘设置')
        title.set_xalign(0)
        root.pack_start(title, False, False, 0)
        self.notice = Gtk.Label(label='设置不会从键盘读取；发送后请检查键盘实际效果。')
        self.notice.set_xalign(0)
        self.notice.set_line_wrap(True)
        root.pack_start(self.notice, False, False, 0)
        device_row = Gtk.Box(spacing=8)
        self.device = Dropdown()
        device_row.pack_start(self.device, True, True, 0)
        self.scan_button = Gtk.Button(label='扫描设备')
        self.scan_button.connect('clicked', lambda *_: self.scan())
        device_row.pack_start(self.scan_button, False, False, 0)
        root.pack_start(device_row, False, False, 0)
        self.tabs = Gtk.Notebook()
        root.pack_start(self.tabs, True, True, 0)
        light = self.page('灯光')
        self.mode = Dropdown()
        for code, name in LIGHT_MODES:
            self.mode.append(str(code), name)
        self.mode.set_active_id('11')
        self.row(light, '灯光模式', self.mode)
        self.color = Gtk.ColorButton()
        self.color.set_title('选择灯光颜色')
        self.color.set_use_alpha(False)
        self.set_color('FFFFFF')
        self.row(light, '颜色', self.color)
        self.brightness = self.number(light, '亮度', 0, 5, 5)
        self.speed = self.number(light, '速度', 0, 5, 3)
        self.colorful = Gtk.CheckButton(label='多彩模式')
        light.pack_start(self.colorful, False, False, 0)
        self.actions(light, self.light_params, 'lighting.set')
        preview_note = Gtk.Label(label='灯光示意预览：点击示意图或在本窗口按下实体按键可触发互动灯效；实际效果以键盘为准。')
        preview_note.set_xalign(0)
        preview_note.set_line_wrap(True)
        light.pack_start(preview_note, False, False, 0)
        self.light_preview = KeyboardView(preview=True)
        light.pack_end(self.light_preview, False, False, 0)
        self.preview_started = time.monotonic()
        self.preview_timer = GLib.timeout_add(120, self.refresh_light_preview)
        mapping = self.page('键位映射')
        self.profile_load_error = None
        try:
            self.profiles = profiles.load()
        except (OSError, ValueError) as exc:
            self.profiles = {}
            self.profile_load_error = str(exc)
        controls = Gtk.Box(spacing=6)
        self.profile_choice = Dropdown()
        self.profile_choice.connect('changed', self.profile_changed)
        controls.pack_start(self.profile_choice, True, True, 0)
        for label, callback in [('新建', self.new_profile), ('删除', self.delete_profile)]:
            button = Gtk.Button(label=label)
            button.connect('clicked', callback)
            controls.pack_start(button, False, False, 0)
            if label == '删除':
                self.delete_profile_button = button
        mapping.pack_start(controls, False, False, 0)
        self.profile_info = Gtk.Label()
        self.profile_info.set_xalign(0)
        self.profile_info.set_line_wrap(True)
        mapping.pack_start(self.profile_info, False, False, 0)
        self.rule_source = KeyboardView()
        self.rule_source.connect('key-selected', self.keyboard_key_selected)
        self.key_label = Gtk.Label(label='请在键盘图上选择按键')
        self.key_label.set_xalign(0)
        mapping.pack_start(self.key_label, False, False, 0)
        self.rules = Gtk.ListStore(str, str, str)
        self.rules_view = Gtk.TreeView(model=self.rules)
        for title, column in [('原按键', 1), ('目标按键或组合键', 2)]:
            self.rules_view.append_column(Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=column))
        self.rules_view.get_selection().connect('changed', self.rule_selected)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(80)
        scroll.add(self.rules_view)
        mapping.pack_start(scroll, True, True, 0)
        edit = Gtk.Box(spacing=6)
        self.rule_target = Gtk.Entry()
        self.rule_target.set_placeholder_text('目标，例如 B 或 CTRL+SHIFT+S')
        edit.pack_start(self.rule_target, True, True, 0)
        self.rule_save = Gtk.Button(label='保存规则')
        self.rule_save.connect('clicked', self.save_rule)
        edit.pack_start(self.rule_save, False, False, 0)
        self.rule_delete = Gtk.Button(label='删除规则')
        self.rule_delete.connect('clicked', self.delete_rule)
        edit.pack_start(self.rule_delete, False, False, 0)
        mapping.pack_start(edit, False, False, 0)
        self.actions(mapping, self.profile_params, 'keymap.apply')
        mapping.pack_end(self.rule_source, False, False, 0)
        self.refresh_profiles(profiles.DEFAULT_NAME)
        firmware = self.page('固件更新')
        info = Gtk.Label(label='随包官方固件 V1.20（2025-07-22），仅支持 USB 有线连接。\n原生更新尚未实机验证；固件与设备的兼容性尚未完全确认。更新会替换整个 Flash，可能丢失配置，且无法自动回滚。')
        info.set_line_wrap(True)
        info.set_xalign(0)
        firmware.pack_start(info, False, False, 0)
        button = Gtk.Button(label='预检固件更新')
        button.connect('clicked', self.prepare_firmware)
        firmware.pack_start(button, False, False, 0)
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        firmware.pack_start(self.progress, False, False, 0)
        self.status = Gtk.Label(label='正在扫描设备…')
        self.status.set_xalign(0)
        self.status.set_line_wrap(True)
        self.status.set_selectable(True)
        root.pack_start(self.status, False, False, 0)
        self.show_all()
        self.scan()

    def page(self, name):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_border_width(16)
        self.tabs.append_page(box, Gtk.Label(label=name))
        return box

    def row(self, box, label, widget):
        row = Gtk.Box(spacing=12)
        text = Gtk.Label(label=label)
        text.set_xalign(0)
        text.set_size_request(180, -1)
        row.pack_start(text, False, False, 0)
        row.pack_start(widget, True, True, 0)
        box.pack_start(row, False, False, 0)

    def entry(self, box, label, value):
        field = Gtk.Entry()
        field.set_text(value)
        self.row(box, label, field)
        return field

    def number(self, box, label, minimum, maximum, value):
        field = Gtk.SpinButton.new_with_range(minimum, maximum, 1)
        field.set_value(value)
        self.row(box, label, field)
        return field

    def actions(self, box, params, method):
        row = Gtk.Box(spacing=8)
        for caption, dry in [('预览', True), ('发送到键盘', False)]:
            button = Gtk.Button(label=caption)
            button.connect('clicked', lambda _, d=dry: self.submit(method, params, d))
            row.pack_start(button, False, False, 0)
        box.pack_start(row, False, False, 0)

    def set_color(self, value):
        color = Gdk.RGBA()
        color.parse('#' + value)
        self.color.set_rgba(color)

    def preview_key_press(self, _window, event):
        if (self.light_preview.get_mapped() and not self.busy and not self.updating
                and self.mode.get_active_id() in ('2', '3', '13', '14', '15')):
            index = index_from_hardware_keycode(event.hardware_keycode)
            if index is not None:
                # Synchronize before recording the event when the mode just changed.
                mode = int(self.mode.get_active_id())
                if self.light_preview.preview_mode != mode:
                    self.light_preview.preview_events.clear()
                    self.light_preview.preview_mode = mode
                self.light_preview.trigger_preview(index)
        return False

    def refresh_light_preview(self):
        if self.light_preview.get_mapped():
            color = self.color.get_rgba()
            self.light_preview.update_lighting(int(self.mode.get_active_id()),
                (color.red, color.green, color.blue), self.brightness.get_value_as_int(),
                self.speed.get_value_as_int(), self.colorful.get_active(),
                time.monotonic() - self.preview_started)
        return True

    def light_params(self):
        color = self.color.get_rgba()
        hex_color = ''.join(f'{round(component * 255):02X}' for component in
                            (color.red, color.green, color.blue))
        return dict(mode=int(self.mode.get_active_id()), color=hex_color, brightness=self.brightness.get_value_as_int(), speed=self.speed.get_value_as_int(), colorful=self.colorful.get_active())

    def selected_profile(self):
        return self.profile_choice.get_active_id()

    def refresh_profiles(self, selected):
        self.profile_choice.remove_all()
        self.profile_choice.append(profiles.DEFAULT_NAME, profiles.DEFAULT_NAME)
        for name in sorted(self.profiles):
            self.profile_choice.append(name, name)
        self.profile_choice.set_active_id(selected)

    def profile_changed(self, *_args):
        name = self.selected_profile()
        if not name:
            return
        readonly = name == profiles.DEFAULT_NAME or self.profile_load_error is not None
        for widget in (self.delete_profile_button, self.rule_source, self.rule_target,
                       self.rule_save, self.rule_delete):
            widget.set_sensitive(not readonly)
        self.rules.clear()
        self.rule_source.clear_selection()
        self.key_label.set_text('请在键盘图上选择按键')
        self.rule_target.set_text('')
        profile = self.current_profile()
        self.rule_source.set_mapped({str(resolve_key(source)['key_index']) for source in profile['mappings']})
        for source, target in profile['mappings'].items():
            key = resolve_key(source)
            index = int(key['key_index'])
            self.rules.append([str(index), '{} (index:{})'.format(key['name'], index), target])
        layer = '普通层' if profile['layer'] == 'normal' else 'Fn 层'
        info = ('默认规则集只读；' if name == profiles.DEFAULT_NAME else '') + layer + '。应用会替换整层，未列出的键恢复默认。规则自动保存到本机。'
        if self.profile_load_error:
            info += '\n规则集文件读取失败，未覆盖原文件：' + self.profile_load_error
        self.profile_info.set_text(info)

    def current_profile(self):
        name = self.selected_profile()
        if name == profiles.DEFAULT_NAME:
            return {'version': 1, 'layer': 'normal', 'mappings': {}}
        return copy.deepcopy(self.profiles[name])

    def profile_params(self):
        if self.profile_load_error:
            raise ValueError('规则集文件读取失败：' + self.profile_load_error)
        return {'profile': self.current_profile()}

    def persist_profiles(self, updated, selected):
        if self.profile_load_error:
            self.status.set_text('规则集文件读取失败，未覆盖原文件：' + self.profile_load_error)
            return False
        try:
            profiles.save(updated)
        except (OSError, ValueError) as exc:
            self.status.set_text('保存失败：' + str(exc))
            return False
        self.profiles = updated
        self.refresh_profiles(selected)
        self.status.set_text('规则集已保存到本机，尚未发送到键盘。')
        return True

    def new_profile(self, *_args):
        if self.profile_load_error:
            self.status.set_text('规则集文件读取失败，未覆盖原文件：' + self.profile_load_error)
            return
        dialog = Gtk.Dialog(title='新建规则集', transient_for=self, modal=True)
        dialog.set_default_size(380, 380)
        dialog.add_buttons('取消', Gtk.ResponseType.CANCEL, '创建', Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        name = Gtk.Entry()
        name.set_placeholder_text('规则集名称')
        layer = Dropdown()
        layer.append('normal', '普通层')
        layer.append('fn', 'Fn 层')
        layer.set_active(0)
        box.pack_start(name, False, False, 0)
        box.pack_start(layer, False, False, 0)
        # GTK popovers are clipped by some Wayland compositors at the dialog edge.
        # Reserve space below the dropdown within the dialog itself.
        spacer = Gtk.Box()
        spacer.set_size_request(-1, 140)
        box.pack_start(spacer, True, True, 0)
        dialog.show_all()
        response = dialog.run()
        new_name = name.get_text().strip()
        new_layer = layer.get_active_id()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        if not new_name or new_name == profiles.DEFAULT_NAME or new_name in self.profiles:
            self.status.set_text('请输入不重复的规则集名称。')
            return
        updated = copy.deepcopy(self.profiles)
        updated[new_name] = {'version': 1, 'layer': new_layer, 'mappings': {}}
        self.persist_profiles(updated, new_name)

    def delete_profile(self, *_args):
        name = self.selected_profile()
        if name == profiles.DEFAULT_NAME or name not in self.profiles:
            return
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK_CANCEL, text='删除规则集“{}”？'.format(name))
        dialog.format_secondary_text('此操作只删除本机保存的规则集，不会更改键盘。')
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            updated = copy.deepcopy(self.profiles)
            del updated[name]
            self.persist_profiles(updated, profiles.DEFAULT_NAME)

    def keyboard_key_selected(self, _view, index):
        key = BY_INDEX[int(index)]
        self.key_label.set_text('已选：{} (index:{})'.format(key['name'], index))
        profile = self.current_profile()
        target = next((target for source, target in profile['mappings'].items()
                       if str(resolve_key(source)['key_index']) == index), '')
        self.rule_target.set_text(target)
        selection = self.rules_view.get_selection()
        selection.unselect_all()
        for row in self.rules:
            if row[0] == index:
                selection.select_iter(row.iter)
                break

    def rule_selected(self, selection):
        model, tree_iter = selection.get_selected()
        if tree_iter:
            index = model[tree_iter][0]
            self.rule_source.set_active_id(index)
            self.key_label.set_text('已选：' + model[tree_iter][1])
            self.rule_target.set_text(model[tree_iter][2])

    def save_rule(self, *_args):
        name = self.selected_profile()
        if name == profiles.DEFAULT_NAME or name not in self.profiles:
            return
        source = self.rule_source.get_active_id()
        target = self.rule_target.get_text().strip()
        if source is None or not target:
            self.status.set_text('请选择原按键并输入目标。')
            return
        updated = copy.deepcopy(self.profiles)
        mapping = updated[name]['mappings']
        for old in list(mapping):
            if str(resolve_key(old)['key_index']) == source:
                del mapping[old]
        mapping['index:' + source] = target
        try:
            parse_profile(updated[name])
        except ValueError as exc:
            self.status.set_text('规则无效：' + str(exc))
            return
        self.persist_profiles(updated, name)

    def delete_rule(self, *_args):
        name = self.selected_profile()
        model, tree_iter = self.rules_view.get_selection().get_selected()
        if name == profiles.DEFAULT_NAME or name not in self.profiles or not tree_iter:
            return
        index = model[tree_iter][0]
        updated = copy.deepcopy(self.profiles)
        mapping = updated[name]['mappings']
        for source in list(mapping):
            if str(resolve_key(source)['key_index']) == index:
                del mapping[source]
        self.persist_profiles(updated, name)

    def call(self, method, params, callback):
        self.busy = True
        self.tabs.set_sensitive(False)
        self.scan_button.set_sensitive(False)
        self.device.set_sensitive(False)
        self.status.set_text('正在处理，请稍候…')
        def worker():
            try:
                response = dispatch({'api_version': 1, 'method': method, 'params': params})
            except Exception as exc:
                response = {'ok': False, 'error': {'message': str(exc)}}
            GLib.idle_add(self.finish, response, callback)
        threading.Thread(target=worker, daemon=True).start()

    def finish(self, response, callback):
        self.busy = False
        self.tabs.set_sensitive(True)
        self.scan_button.set_sensitive(True)
        self.device.set_sensitive(True)
        if response.get('ok'):
            callback(response['result'])
        else:
            self.updating = False
            error = response.get('error', {})
            message = error.get('message', '未知错误')
            if error.get('may_have_partial_changes'):
                message += '\n设备可能已部分更改，请检查键盘。'
            self.status.set_text('操作失败：' + message)
        return False

    def scan(self):
        self.call('devices.scan', {}, self.scanned)

    def scanned(self, result):
        previous_index = self.device.get_active()
        previous_id = (self.devices[previous_index]['id']
                       if 0 <= previous_index < len(self.devices) else None)
        self.devices = result['devices']
        self.device.remove_all()
        for item in self.devices:
            self.device.append(item['id'], '{} · {} · {}'.format(
                item['name'], '/'.join(item['connections']), item['access_status']))
        if self.devices:
            if not previous_id or not self.device.set_active_id(previous_id):
                self.device.set_active(0)
            self.status.set_text('请选择设备。ready 表示配置节点可访问。')
        else:
            self.status.set_text('未找到设备。请连接键盘或 2.4G 接收器后重新扫描。')

    def submit(self, method, make_params, dry):
        index = self.device.get_active()
        if index < 0:
            self.status.set_text('请先扫描并选择设备。')
            return
        try:
            params = make_params()
        except (ValueError, TypeError) as exc:
            self.status.set_text('配置格式错误：' + str(exc))
            return
        params.update(device=self.devices[index]['id'], dry_run=dry)
        self.call(method, params, self.completed)

    def on_close(self, *_args):
        if self.updating:
            self.status.set_text('固件更新正在进行，请保持连接和供电，等待完成后再关闭。')
            return True
        return False

    def prepare_firmware(self, *_args):
        index = self.device.get_active()
        if index < 0:
            self.status.set_text('请先扫描并选择有线设备。')
            return
        device_id = self.devices[index]['id']
        self.call('firmware.prepare', {'device': device_id},
                  lambda result: self.confirm_firmware(device_id, result))

    def confirm_firmware(self, device_id, result):
        fw = result['firmware']
        message = ('设备：{}\n固件：{}（{}）\n\n{}\n\n'
                   '预检尚未进入升级模式；开始后会核对设备响应。请保持有线连接和供电。'
                   '更新失败可能导致键盘无法使用，可能需要官方 Windows 工具恢复。\n\n确定开始更新？').format(
                       result['device']['name'], fw['version_label'], fw['build_date_label'],
                       '\n'.join(result['warnings']))
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.NONE, text='确认固件更新')
        dialog.format_secondary_text(message)
        dialog.add_button('取消', Gtk.ResponseType.CANCEL)
        dialog.add_button('开始更新', Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self.updating = True
            self.call('firmware.update', {'device': device_id}, self.firmware_started)

    def firmware_started(self, result):
        self.tabs.set_sensitive(False)
        self.scan_button.set_sensitive(False)
        self.device.set_sensitive(False)
        self.poll_firmware(result['job_id'])

    def poll_firmware(self, job_id):
        def worker():
            try:
                response = dispatch({'api_version': 1, 'method': 'firmware.status',
                                     'params': {'job_id': job_id}})
            except Exception as exc:
                response = {'ok': False, 'error': {'message': str(exc)}}
            GLib.idle_add(self.firmware_status, job_id, response)
        threading.Thread(target=worker, daemon=True).start()
        return False

    def firmware_status(self, job_id, response):
        if not response.get('ok'):
            self.status.set_text('无法查询更新状态：' + response.get('error', {}).get('message', '未知错误') + '；请勿断开连接。')
            GLib.timeout_add(1000, self.poll_firmware, job_id)
            return False
        result = response['result']
        total = result.get('total_bytes', 0)
        written = result.get('written_bytes', 0)
        self.progress.set_fraction(min(1.0, written / total) if total else 0)
        self.progress.set_text('{} / {} 字节'.format(written, total))
        state = result['status']
        self.status.set_text('固件更新阶段：' + result['stage'])
        if state == 'running':
            GLib.timeout_add(500, self.poll_firmware, job_id)
        else:
            self.updating = False
            self.tabs.set_sensitive(True)
            self.scan_button.set_sensitive(True)
            self.device.set_sensitive(True)
            if state == 'completed':
                self.status.set_text('更新完成：校验通过且键盘已重新连接。请检查键盘功能。')
            else:
                detail = ('已开始擦写，设备可能已有部分更改，请勿自动重试；可能需要官方工具恢复。'
                          if result.get('may_have_partial_changes') else
                          '尚未执行擦除或固件写入；已退出 ISP，键盘已重新连接。'
                          if result.get('normal_device_returned') else
                          '尚未执行擦除或固件写入；已尝试切换 ISP，若键盘暂时不可用，可重新开启键盘恢复。'
                          if result.get('device_changed') else '尚未向设备发送更新命令。')
                self.status.set_text('更新失败（阶段 {}）：{}。{}'.format(
                    result['stage'], result.get('error', {}).get('message', '未知错误'), detail))
        return False

    def completed(self, result):
        if result.get('dry_run'):
            message = '预览成功，尚未发送。'
        else:
            message = '已发送，请确认键盘效果；生效和持久保存尚未验证。'
        warnings = result.get('warnings', [])
        if warnings:
            message += '\n' + '\n'.join(map(str, warnings))
        self.status.set_text(message)


def main():
    GLib.set_prgname('ganss-gs3104t')
    GLib.set_application_name('GANSS GS3104T PRO')
    Gtk.Window.set_default_icon_name('ganss-gs3104t')
    win = Window()
    win.connect('destroy', Gtk.main_quit)
    Gtk.main()


if __name__ == '__main__':
    main()
