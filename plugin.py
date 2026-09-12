from __future__ import annotations

from functools import partial
from LSP.plugin import ClientRequest
from LSP.plugin import LspPlugin
from LSP.plugin import LspTextCommand
from LSP.plugin import OnPreStartContext
from LSP.plugin import Promise
from LSP.plugin import Request
from LSP.plugin import ServerResponse
from LSP.plugin import Session
from LSP.plugin import uri_handler
from LSP.plugin.core.registry import get_position
from LSP.plugin.core.views import get_symbol_kind_from_scope
from LSP.plugin.core.views import position_to_offset
from LSP.plugin.core.views import text_document_position_params
from LSP.plugin.locationpicker import LocationPicker
from LSP.plugin.locationpicker import open_location_async
from LSP.protocol import DocumentUri
from LSP.protocol import Hover
from LSP.protocol import HoverParams
from LSP.protocol import Location
from LSP.protocol import LocationLink
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
            Path('node_modules', '@typescript', 'native-preview', 'lib', 'tsgo.js'),
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


# tsgo's "Go to Source Definition": resolves a declaration in a .d.ts to the .js (or, through a
# declaration map, the .ts) it was generated from. It is a custom request rather than a standard one,
# the same the VS Code extension for tsgo sends, and the server announces it in its experimental
# capabilities.
SOURCE_DEFINITION_METHOD = 'custom/textDocument/sourceDefinition'
SOURCE_DEFINITION_CAPABILITY = 'experimental.customSourceDefinitionProvider'


class LspTsgoGotoSourceDefinitionCommand(LspTextCommand):
    """Go to the implementation behind a declaration file.

    With `fallback` (the default) the ordinary definition is used when the server has no source
    definition to offer, e.g. the symbol already lives in TypeScript source, or the server predates
    the request. The command is then always safe to bind in place of `lsp_symbol_definition`.
    """

    capability = 'definitionProvider'

    def run(
        self,
        _: sublime.Edit,
        event: dict | None = None,
        point: int | None = None,
        side_by_side: bool = False,
        force_group: bool = True,
        group: int = -1,
        fallback: bool = True,
    ) -> None:
        position = get_position(self.view, event, point)
        session = self.best_session(SOURCE_DEFINITION_CAPABILITY, position)
        if session is None or position is None:
            self._fallback(fallback, side_by_side, force_group, group)
            return
        params = text_document_position_params(self.view, position)
        request: Request[Any, Location | list[Location] | list[LocationLink] | None] = Request(
            SOURCE_DEFINITION_METHOD, params, self.view, progress=True
        )
        session.send_request(
            request,
            partial(self._on_result_async, session, side_by_side, force_group, group, position, fallback),
            lambda _error: self._fallback(fallback, side_by_side, force_group, group),
        )

    def _on_result_async(
        self,
        session: Session,
        side_by_side: bool,
        force_group: bool,
        group: int,
        position: int,
        fallback: bool,
        response: Location | list[Location] | list[LocationLink] | None,
    ) -> None:
        if isinstance(response, dict):
            locations: list[Location] | list[LocationLink] = [response]
        elif isinstance(response, list):
            locations = response
        else:
            locations = []
        if not locations:
            self._fallback(fallback, side_by_side, force_group, group)
            return
        self.view.run_command('add_jump_record', {'selection': [(r.a, r.b) for r in self.view.sel()]})
        if len(locations) == 1:
            open_location_async(session, locations[0], side_by_side, force_group, group)
            return
        placeholder = 'Source definitions of ' + self.view.substr(self.view.word(position))
        kind = get_symbol_kind_from_scope(self.view.scope_name(position))
        sublime.set_timeout(
            partial(LocationPicker, self.view, session, locations, side_by_side, force_group, group, placeholder, kind)
        )

    def _fallback(self, fallback: bool, side_by_side: bool, force_group: bool, group: int) -> None:
        if not fallback:
            sublime.status_message('No source definition found')
            return
        self.view.run_command(
            'lsp_symbol_definition',
            {'side_by_side': side_by_side, 'force_group': force_group, 'group': group, 'fallback': True},
        )
