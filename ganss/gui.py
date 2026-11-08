"""GTK 3 desktop interface for the GS3104T PRO."""
import json
import threading

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib, Gdk
except (ImportError, ValueError) as exc:
    raise SystemExit('需要安装 GTK 3 和 Python GObject（python-gobject / python3-gi）。') from exc

from .api import dispatch
from .light_modes import LIGHT_MODES


class Window(Gtk.Window):
    def __init__(self):
        super().__init__(title='GANSS GS3104T PRO')
        self.set_default_size(720, 620)
        self.set_border_width(20)
        self.devices = []
        self.busy = False
        self.updating = False
        self.connect('delete-event', self.on_close)
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
        self.device = Gtk.ComboBoxText()
        device_row.pack_start(self.device, True, True, 0)
        self.scan_button = Gtk.Button(label='扫描设备')
        self.scan_button.connect('clicked', lambda *_: self.scan())
        device_row.pack_start(self.scan_button, False, False, 0)
        root.pack_start(device_row, False, False, 0)
        self.tabs = Gtk.Notebook()
        root.pack_start(self.tabs, True, True, 0)
        light = self.page('灯光')
        self.mode = Gtk.ComboBoxText()
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
        mapping = self.page('单键映射')
        self.layer = Gtk.ComboBoxText()
        self.layer.append('normal', '普通层')
        self.layer.append('fn', 'Fn 层')
        self.layer.set_active(0)
        self.row(mapping, '层', self.layer)
        self.source = self.entry(mapping, '原按键', 'A')
        self.target = self.entry(mapping, '目标按键或组合键', 'B')
        hint = Gtk.Label(label='例如 A、CTRL+SHIFT+S；普通层可用 default 恢复。单键更新仅支持 2.4G。')
        hint.set_line_wrap(True)
        hint.set_xalign(0)
        mapping.pack_start(hint, False, False, 0)
        self.actions(mapping, self.map_params, 'keymap.set')
        profile = self.page('整层配置')
        label = Gtk.Label(label='输入 JSON 配置。应用会替换整层，未列出的按键恢复默认。')
        label.set_xalign(0)
        label.set_line_wrap(True)
        profile.pack_start(label, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        self.profile = Gtk.TextView()
        self.profile.set_monospace(True)
        self.profile.get_buffer().set_text(json.dumps({'version': 1, 'layer': 'normal', 'mappings': {'A': 'B'}}, indent=2))
        scroll.add(self.profile)
        scroll.set_min_content_height(160)
        profile.pack_start(scroll, True, True, 0)
        self.actions(profile, self.profile_params, 'keymap.apply')
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

    def light_params(self):
        color = self.color.get_rgba()
        hex_color = ''.join(f'{round(component * 255):02X}' for component in
                            (color.red, color.green, color.blue))
        return dict(mode=int(self.mode.get_active_id()), color=hex_color, brightness=self.brightness.get_value_as_int(), speed=self.speed.get_value_as_int(), colorful=self.colorful.get_active())

    def map_params(self):
        return dict(layer=self.layer.get_active_id(), source=self.source.get_text().strip(), target=self.target.get_text().strip())

    def profile_params(self):
        buf = self.profile.get_buffer()
        return {'profile': json.loads(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True))}

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
        self.devices = result['devices']
        self.device.remove_all()
        for item in self.devices:
            self.device.append_text('{} · {} · {}'.format(item['name'], '/'.join(item['connections']), item['access_status']))
        if self.devices:
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
