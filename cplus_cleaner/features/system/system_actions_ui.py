"""System maintenance commands shared by the dedicated page and paging shortcut."""
from cplus_cleaner.localization.localized_tk import messagebox
from cplus_cleaner.features.system import cdrive_system
from cplus_cleaner.ui.ui_tasks import operation_status


SYSTEM_ACTIONS = {
    "empty_recycle_bin": ("清空系统盘回收站 · 永久删除", "永久删除系统盘回收站中的全部项目，无法从回收站恢复。"),
    "hibernate_off": ("关闭休眠 · 管理员", "删除休眠文件并关闭休眠；也会关闭快速启动。需要管理员权限。"),
    "hibernate_on": ("恢复休眠 · 管理员", "启用休眠并重新分配休眠文件空间；恢复快速启动所需的休眠支持。需要管理员权限。"),
    "analyze_component_store": ("DISM 分析组件存储 · 管理员", "仅分析 Windows 组件存储，不删除文件。需要管理员权限，可能耗时较长。"),
    "cleanup_component_store": ("DISM 组件清理 · 管理员", "通过 Windows DISM 清理被替代的组件，可能影响旧组件回退。需要管理员权限，可能耗时较长。"),
    "pagefile_settings": ("打开虚拟内存设置", "打开 Windows 虚拟内存设置，由您手动调整；本工具不会删除 pagefile.sys。保存系统设置可能需要管理员权限及重启。"),
}


def run_system_action(self, action_id):
    if self.app.busy or action_id not in self.SYSTEM_ACTIONS:
        return
    available = {action for item in self.system_items for action in item.get("actions", [])}
    if action_id not in available:
        operation_status(self, "请先只读刷新系统项，查看当前可用操作")
        return
    label, explanation = self.SYSTEM_ACTIONS[action_id]
    if action_id in ("empty_recycle_bin", "hibernate_off", "hibernate_on", "cleanup_component_store"):
        if not messagebox.askyesno(label, explanation + "\n\n确认执行此系统操作？", parent=self):
            return
    begin = getattr(self, 'begin_system_action', None)
    if begin:
        begin(action_id, label)
    report = getattr(self, 'report_system_progress', self.app.report_progress)
    def work():
        options = dict(progress=report)
        if action_id == 'analyze_component_store' and getattr(self, 'allow_analysis_elevation', False):
            options['allow_elevation'] = True
        return cdrive_system.run_action(action_id, **options)
    def done(result):
        finish = getattr(self, 'finish_system_action', None)
        if finish:
            finish(action_id, result)
        operation_status(self, result.get("message") or
                         f"{label}{'完成' if result.get('ok') else '未完成'}")
        self.refresh_system()
    def failed(error):
        finish = getattr(self, 'finish_system_action', None)
        if finish:
            finish(action_id, dict(ok=False, message=error))
        operation_status(self, error)
    self.app.start(label, work, done, failed)
