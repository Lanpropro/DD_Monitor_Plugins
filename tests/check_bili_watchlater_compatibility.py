"""Source-only compatibility gate; no ZIP creation or account access."""
from pathlib import Path
from types import SimpleNamespace
import json,subprocess,unittest
from unittest.mock import Mock,patch
from check_bili_watchlater import HOST,ROOT,plugin_module

NEW_APIS=('register_sidebar_page','unregister_sidebar_page','start_sidebar_drag','cancel_sidebar_drag')
BASE_APIS=('register_workspace_control','unregister_workspace_control','show_workspace','credentials','request_login','notify')


class CompatibilityTests(unittest.TestCase):
    def context(self):
        values={name:Mock() for name in BASE_APIS+NEW_APIS}
        values.update(window=None,name='bili_watchlater',setting=lambda key,default=None:default,set_setting=Mock())
        return SimpleNamespace(**values)

    def test_missing_new_api_is_rejected_before_context_or_sidebar_mutation(self):
        for name in NEW_APIS:
            with self.subTest(api=name):
                context=self.context()
                delattr(context,name)
                plugin=plugin_module.BiliWatchLaterPlugin()
                with self.assertRaisesRegex(RuntimeError,name):plugin.on_load(context)
                self.assertIsNone(plugin.context)
                context.register_workspace_control.assert_not_called()

    def test_released_context_gets_actionable_upgrade_message(self):
        context=self.context()
        for name in NEW_APIS:delattr(context,name)
        with self.assertRaisesRegex(RuntimeError,'晚于已发布的 v0.7.3'):
            plugin_module.BiliWatchLaterPlugin().on_load(context)

    def test_capable_headless_context_still_loads_and_unloads(self):
        plugin=plugin_module.BiliWatchLaterPlugin()
        context=self.context()
        plugin.on_load(context)
        self.assertIs(plugin.context,context)
        plugin.on_unload()
        self.assertTrue(plugin._shutting_down)

    def test_missing_live_source_capability_is_rejected_before_mutation(self):
        plugin=plugin_module.BiliWatchLaterPlugin()
        context=self.context()
        with patch.object(plugin_module.api,'ROOM_SUPPORTS_LIVE_DANMAKU',None), \
                self.assertRaisesRegex(RuntimeError,'ROOM_SUPPORTS_LIVE_DANMAKU'):
            plugin.on_load(context)
        self.assertIsNone(plugin.context)
        context.register_workspace_control.assert_not_called()

    def test_actual_release_tag_lacks_required_sidebar_contract(self):
        source=subprocess.check_output(['git','-C',str(HOST),'show','v0.7.3:ddm/plugin_types.py'],text=True,encoding='utf-8')
        from ddm.plugin_types import PluginContext
        for name in NEW_APIS:
            self.assertNotIn('def '+name+'(',source)
            self.assertTrue(callable(getattr(PluginContext,name,None)),name)

    def test_manifest_and_release_draft_record_the_unreleased_dependency(self):
        manifest=json.loads((ROOT/'plugins/bili_watchlater/plugin.json').read_text(encoding='utf-8'))
        self.assertIn('sidebar_pages',manifest['host_capabilities'])
        self.assertIn('shared_sidebar_drag',manifest['host_capabilities'])
        self.assertIn('live_danmaku_source_filter',manifest['host_capabilities'])
        draft=(ROOT/'docs/releases/DRAFT-bili-watchlater-v1.0.md').read_text(encoding='utf-8')
        for text in ('v0.7.3','min_app_version','未正式发布','ROOM_SUPPORTS_LIVE_DANMAKU',*NEW_APIS):
            self.assertIn(text,draft)


if __name__=='__main__':unittest.main()
