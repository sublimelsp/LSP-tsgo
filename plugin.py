from __future__ import annotations

from LSP.plugin import ClientRequest
from LSP.plugin import LspPlugin
from LSP.plugin import OnPreStartContext
from LSP.plugin import Promise
from LSP.plugin import ServerResponse
from LSP.plugin import Session
from LSP.plugin import uri_handler
from LSP.plugin.core.views import position_to_offset
from LSP.protocol import DocumentUri
from LSP.protocol import Hover
from LSP.protocol import HoverParams
from lsp_utils import NodeManager
from pathlib import Path
from sublime_lib import ResourcePath
from typing import Any
from typing import cast
from typing import final
from typing_extensions import NotRequired
from typing_extensions import override
import sublime


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
            node_version_requirement='>=20',
        )

    @override
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._verbosity_hover_handler = VerbosityHoverHandler()

    def on_pre_send_request_async(self, request: ClientRequest, view: sublime.View | None) -> None:
        if request['method'] == 'textDocument/hover':
            self._verbosity_hover_handler.on_hover_request(cast('HoverParamsWithVerbosity', request['params']))
            return

    def on_server_response_async(self, response: ServerResponse) -> None:
        if response['method'] == 'textDocument/hover':
            self._verbosity_hover_handler.on_hover_response(cast('VerbosityHover | None', response['result']))
            return

    @uri_handler('verbosity')
    def handle_verbosity_uri(self, uri: DocumentUri, _: sublime.NewFileFlags) -> Promise[sublime.Sheet | None]:
        if session := self.weaksession():
            verbosity_delta = int(uri.split(':')[1])
            self._verbosity_hover_handler.handle_verbosity_change(session, verbosity_delta)
        return Promise.resolve(None)


def plugin_loaded() -> None:
    LspTsgoPlugin.register()


def plugin_unloaded() -> None:
    LspTsgoPlugin.unregister()


class HoverParamsWithVerbosity(HoverParams):
    verbosityLevel: NotRequired[int]


class VerbosityHover(Hover):
    canIncreaseVerbosity: NotRequired[bool]


class VerbosityHoverHandler:

    def __init__(self) -> None:
        self._last_hover_params: HoverParamsWithVerbosity | None = None

    def on_hover_request(self, hover_params: HoverParamsWithVerbosity) -> None:
        if (
            self._last_hover_params
            and self._last_hover_params['position'] == hover_params['position']
            and self._last_hover_params['textDocument']['uri'] == hover_params['textDocument']['uri']
        ):
            verbosity_level = self._last_hover_params.get('verbosityLevel', 0)
        else:
            verbosity_level = 0
        self._last_hover_params = hover_params
        hover_params['verbosityLevel'] = verbosity_level

    def on_hover_response(self, hover_response: VerbosityHover | None) -> None:
        if not hover_response:
            self._last_hover_params = None
            return
        if (
            self._last_hover_params
            and (contents := hover_response['contents']) and isinstance(contents, dict) and 'kind' in contents
        ):
            verbosity_level = self._last_hover_params.get('verbosityLevel', 0)
            can_increase_verbosity = hover_response.get('canIncreaseVerbosity')
            if verbosity_level > 0 or can_increase_verbosity:
                controls: list[str] = [
                    f'<kbd>{"[-](verbosity:-1)" if verbosity_level > 0 else "-"}</kbd>',
                    f'<kbd>{"[+](verbosity:+1)" if can_increase_verbosity else "+"}</kbd>',
                ]
                contents['value'] = f'{" ".join(controls)}\n{contents["value"]}'

    def handle_verbosity_change(self, session: Session, verbosity_delta: int) -> None:
        if not self._last_hover_params:
            return
        hover_params = self._last_hover_params
        verbosity_level = max(0, hover_params.get('verbosityLevel', 0) + verbosity_delta)
        hover_params['verbosityLevel'] = verbosity_level
        if session_buffer := session.get_session_buffer_for_uri_async(hover_params['textDocument']['uri']):
            view = session_buffer.get_view_in_group()
            point = position_to_offset(hover_params['position'], view)
            view.run_command('lsp_hover', {'point': point})
