from __future__ import annotations

from LSP.plugin import ClientRequest
from LSP.plugin import Error
from LSP.plugin import LspPlugin
from LSP.plugin import LspTextCommand
from LSP.plugin import OnPreStartContext
from LSP.plugin import position_to_offset
from LSP.plugin import Promise
from LSP.plugin import Request
from LSP.plugin import ServerResponse
from LSP.plugin import Session
from LSP.plugin import text_document_position_params
from LSP.plugin import uri_handler
from LSP.protocol import DocumentUri
from LSP.protocol import Hover
from LSP.protocol import HoverParams
from LSP.protocol import Location
from LSP.protocol import LocationLink
from LSP.protocol import TextDocumentPositionParams
from LSP.protocol import InitializeResult
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
            Path('node_modules', 'typescript', 'lib', 'tsc.js'),
            node_version_requirement='>=20',
        )

    def _show_typescript_version(self, result: InitializeResult) -> None:
        session = self.weaksession()
        status_text = session.config.root_settings.get('status_text') if session else None
        if not session or not status_text or not isinstance(status_text, str):
            return
        version = result.get('serverInfo', {}).get('version', '')
        server_path = session.config.root_settings.get('server_path')
        source = 'bundled' if not server_path or server_path == 'auto' else 'server_path'
        session.set_config_status_async(status_text.replace('$version', version).replace('$source', source))

    @override
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._verbosity_hover_handler = VerbosityHoverHandler()

    def on_pre_send_request_async(self, request: ClientRequest, view: sublime.View | None) -> None:
        if request['method'] == 'textDocument/hover':
            self._verbosity_hover_handler.on_hover_request(cast('HoverParamsWithVerbosity', request['params']))
            return

    def on_server_response_async(self, response: ServerResponse) -> None:
        if response['method'] == 'initialize':
            self._show_typescript_version(cast('InitializeResult', response['result']))
            return
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
            point = position_to_offset(view, hover_params['position'])
            view.run_command('lsp_hover', {'point': point})




class LspTsgoGotoSourceDefinitionCommand(LspTextCommand):
    """Go to the implementation behind a declaration file.

    With `fallback` (the default) the ordinary definition is used when the server has no source
    definition to offer, e.g. the symbol already lives in TypeScript source, or the server predates
    the request. The command is then always safe to bind in place of `lsp_symbol_definition`.
    """

    SOURCE_DEFINITION_METHOD = 'custom/textDocument/sourceDefinition'
    SOURCE_DEFINITION_CAPABILITY = 'experimental.customSourceDefinitionProvider'

    capability = 'definitionProvider'

    def run(
        self, _: sublime.Edit, event: dict[str, int] | None = None, point: int | None = None, fallback: bool = True
    ) -> None:
        if point is None and event and 'x' in event and 'y' in event:
            point = self.view.window_to_text((event['x'], event['y']))
        position = point if point is not None else next((r.b for r in self.view.sel()), None)
        session = self.best_session(self.SOURCE_DEFINITION_CAPABILITY, position)
        if session is None or position is None:
            self._fallback(fallback)
            return
        params = text_document_position_params(self.view, position)
        request: Request[Any, Location | list[Location] | list[LocationLink] | None] = Request(
            self.SOURCE_DEFINITION_METHOD, params, self.view, progress=True
        )
        session.send_request_task(request) \
            .then(lambda response: self._on_result_async(session, params, fallback, response))

    def _on_result_async(
        self,
        session: Session,
        params: TextDocumentPositionParams,
        fallback: bool,
        response: Location | list[Location] | list[LocationLink] | Error | None,
    ) -> None:
        if not response or isinstance(response, Error):
            self._fallback(fallback)
            return
        locations = response if isinstance(response, list) else [response]
        # Handled by LSP itself: opens a single location directly, or shows the location picker.
        session.execute_command({
            'command': 'editor.action.showReferences',
            'arguments': [params['textDocument']['uri'], params['position'], locations],
        }, view=self.view)

    def _fallback(self, fallback: bool) -> None:
        if not fallback:
            sublime.status_message('No source definition found')
            return
        self.view.run_command('lsp_symbol_definition', {'fallback': True})
