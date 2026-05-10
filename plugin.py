from __future__ import annotations

from LSP.plugin import LspPlugin
from LSP.plugin import OnPreStartContext
from lsp_utils import NodeManager
from pathlib import Path
from sublime_lib import ResourcePath
from typing import final
from typing_extensions import override

VSCODE_RELEASE_TAG = '1.119.0'

@final
class LspTsgoPlugin(LspPlugin):

    @classmethod
    @override
    def on_pre_start_async(cls, context: OnPreStartContext) -> None:
        package_name = cls.plugin_storage_path.name
        NodeManager.on_pre_start_async(
            context,
            cls.plugin_storage_path,
            ResourcePath('Packages', package_name, 'server'),
            Path('node_modules', '@typescript', 'native-preview', 'bin', 'tsgo.js'),
            '>=20',
        )


def plugin_loaded() -> None:
    LspTsgoPlugin.register()


def plugin_unloaded() -> None:
    LspTsgoPlugin.unregister()
