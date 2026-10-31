from __future__ import annotations

from LSP.plugin import ClientRequest
from LSP.plugin import Error
from LSP.plugin import IsApplicableContext
from LSP.plugin import LspPlugin
from LSP.plugin import LspTextCommand
from LSP.plugin import OnPreStartContext
from LSP.plugin import parse_uri
from LSP.plugin import position_to_offset
from LSP.plugin import Promise
from LSP.plugin import Request
from LSP.plugin import ServerResponse
from LSP.plugin import Session
from LSP.plugin import text_document_position_params
from LSP.plugin import uri_from_view
from LSP.plugin import uri_handler
from LSP.protocol import DocumentUri
from LSP.protocol import Hover
from LSP.protocol import HoverParams
from LSP.protocol import InitializeResult
from LSP.protocol import Location
from LSP.protocol import LocationLink
from LSP.protocol import LSPAny
from LSP.protocol import TextDocumentPositionParams
from lsp_utils import NodeManager
from pathlib import Path
from sublime_lib import ResourcePath
from typing import Any
from typing import cast
from typing import final
from typing import TypedDict
from typing_extensions import NotRequired
from typing_extensions import override
from weakref import WeakSet
import json
import os
import sublime
import sublime_plugin


def log(message: str) -> None:
    print(f'[{__package__}] {message}')


class ContentMapperContribution(TypedDict):
    """An entry of a `typescript-content-mappers.json` file that another package contributes."""
    # Name of the content mapper's npm package.
    name: str
    # File extensions that the content mapper handles.
    extensions: list[str]
    # Selector of the views that this server attaches to, for the files that the content mapper handles.
    selector: NotRequired[str]
    # Content mapper options, used for files outside any tsconfig.
    options: NotRequired[dict[str, LSPAny]]
    # Name of the package that contributes the content mapper.
    contributor: NotRequired[str]


def find_content_mapper_contributions() -> list[ContentMapperContribution]:
    contributions: list[ContentMapperContribution] = []
    for resource in ResourcePath.glob_resources('typescript-content-mappers.json'):
        try:
            contributed = sublime.decode_value(resource.read_text())
        except ValueError:
            log(f'Failed parsing "{resource.file_path()}"')
            continue
        if not isinstance(contributed, list):
            log(f'Invalid contents of "{resource.file_path()}"')
            continue
        for entry in cast('list[ContentMapperContribution]', contributed):
            contribution: ContentMapperContribution = {
                'name': entry['name'],
                'extensions': entry['extensions'],
                'contributor': resource.parts[1],
            }
            if 'selector' in entry:
                contribution['selector'] = entry['selector']
            if 'options' in entry:
                contribution['options'] = entry['options']
            contributions.append(contribution)
    return contributions


def to_server_contribution(contribution: ContentMapperContribution, location: str) -> dict[str, LSPAny] | None:
    """
    Converts a contribution to the format of `custom/setContentMapperContributions`, with the manifest from the
    `typescript.contentMapper` field of the content mapper's `package.json`.

    :param location: The directory containing the content mapper's package, as reported by its contributor.
    """
    package_path = Path(location) / contribution['name']
    try:
        package_json = json.loads((package_path / 'package.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        log(f'Ignoring content mapper with an unreadable package.json at "{package_path}": {error}')
        return None
    try:
        manifest = package_json['typescript']['contentMapper']
    except (KeyError, TypeError):
        log(f'Ignoring content mapper without a manifest at "{package_path}"')
        return None
    server_manifest: dict[str, LSPAny] = {
        'name': package_json.get('name', contribution['name']),
        'exec': manifest['exec'],
        # The content mapper resolves `exec` relative to this directory.
        'cwd': str(package_path.resolve()),
    }
    if 'version' in package_json:
        server_manifest['version'] = package_json['version']
    for key in ('compilerOptions', 'dynamicConfig', 'outputExtensions'):
        if key in manifest:
            server_manifest[key] = manifest[key]
    return {
        'contributorId': contribution.get('contributor', ''),
        'extensions': contribution['extensions'],
        'inferredProjectContribution': {
            'options': contribution.get('options', {}),
            'manifest': server_manifest,
        },
    }


@final
class LspTsgoPlugin(LspPlugin):
    content_mapper_contributions: list[ContentMapperContribution] | None = None
    # Directories containing the content mappers, by name. Reported by the contributors once they know them.
    content_mapper_locations: dict[str, str] = {}
    initialized_plugins: WeakSet[LspTsgoPlugin] = WeakSet()
    resolved_server_direcotry: Path | None = None

    @classmethod
    @override
    def is_applicable_async(cls, context: IsApplicableContext) -> bool:
        if super().is_applicable_async(context):
            return True
        # Content mappers only run with `runExternalCode`, so other files are left to their own servers without it.
        # It is enabled by default.
        if not context.configuration.initialization_options.get('runExternalCode'):
            return False
        scheme, _ = parse_uri(uri_from_view(context.view))
        if scheme in context.configuration.schemes and (syntax := context.view.syntax()):
            # Projects with a tsconfig load their content mappers from the project, so this doesn't check whether the
            # contributed content mapper is installed.
            for contribution in cls._get_content_mapper_contributions():
                if (selector := contribution.get('selector')) and sublime.score_selector(syntax.scope, selector) > 0:
                    return True
        return False

    @classmethod
    @override
    def on_pre_start_async(cls, context: OnPreStartContext) -> None:
        package_name = cls.plugin_storage_path.name
        resolved_server_direcotry = NodeManager.on_pre_start_async(
            context,
            cls.plugin_storage_path,
            ResourcePath('Packages', package_name, 'server'),
            Path('node_modules', 'typescript', 'lib', 'tsc.js'),
            # Also covers content mappers, which run on the same Node.js (e.g. `@vue/content-mapper`).
            node_version_requirement='^20.19.0 || >=22.12.0',
        )
        if resolved_server_direcotry:
            cls.resolved_server_direcotry = resolved_server_direcotry
        # Content mappers start with `node` from the server's PATH. Put the Node.js that runs the server first, so
        # that mappers use the same runtime even if it was installed by lsp_utils and is not on the system PATH.
        node_directory = str(Path(context.variables['node_bin']).parent)
        env_path: str | list[str] = context.configuration.env.get('PATH') or []
        paths = env_path.split(os.pathsep) if isinstance(env_path, str) else list(env_path)
        if node_directory not in paths:
            context.configuration.env['PATH'] = os.pathsep.join([node_directory, *paths])

    @classmethod
    def _get_content_mapper_contributions(cls) -> list[ContentMapperContribution]:
        if cls.content_mapper_contributions is None:
            cls.content_mapper_contributions = find_content_mapper_contributions()
        return cls.content_mapper_contributions

    @classmethod
    def set_content_mapper_location_async(cls, name: str, location: str) -> None:
        if cls.content_mapper_locations.get(name) == location:
            return
        cls.content_mapper_locations[name] = location
        for plugin in cls.initialized_plugins:
            plugin._register_content_mappers_async()

    @override
    def on_initialized_async(self) -> None:
        self.initialized_plugins.add(self)
        self._register_content_mappers_async()

    def _register_content_mappers_async(self) -> None:
        """
        Registers the contributed content mappers for files outside any tsconfig. The request replaces the previously
        registered ones, so it includes all with a known location.
        """
        session = self.weaksession()
        if not session or not session.config.initialization_options.get('runExternalCode'):
            return
        locations = self.content_mapper_locations
        contributions = [c for c in self._get_content_mapper_contributions() if c['name'] in locations]
        if not contributions:
            return
        server_contributions = [
            server_contribution for c in contributions
            if (server_contribution := to_server_contribution(c, locations[c['name']]))
        ]
        extensions = tuple(extension for c in contributions for extension in c['extensions'])
        open_documents = [
            {'uri': uri} for sb in session.session_buffers_async()
            if (uri := sb.get_uri()) and uri.endswith(extensions)
        ]
        session.send_request_task(Request('custom/setContentMapperContributions', {
            'contributions': server_contributions,
            'openDocuments': open_documents,
        })).then(self._on_set_content_mapper_contributions)

    def _on_set_content_mapper_contributions(self, result: None | Error) -> None:
        if isinstance(result, Error):
            log(f'Failed to register content mappers: {result}')

    def _get_typescript_package_json(self, session: Session) -> Path | None:
        if self.resolved_server_direcotry:
            return self.resolved_server_direcotry / 'node_modules' / 'typescript' / 'package.json'
        server_path = cast(str, session.config.root_settings.get('server_path'))
        # A custom server path points into a TypeScript installation, e.g. `node_modules/typescript/lib/tsc.js`.
        for directory in Path(os.path.expanduser(server_path)).resolve().parents:
            if (package_json := directory / 'package.json').is_file():
                return package_json
        return None

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
            self._show_typescript_version(response['result'])
            return
        if response['method'] == 'textDocument/hover':
            self._verbosity_hover_handler.on_hover_response(cast('VerbosityHover | None', response['result']))
            return
        if response['method'] == 'custom/initializeAPISession':
            self._on_initialize_api_session_response(response)
            return

    def _on_initialize_api_session_response(self, response: ServerResponse) -> None:
        """
        Other packages (e.g. LSP-vue) open a session of the TypeScript API with a `custom/initializeAPISession` request
        to this server. Add the `package.json` of the TypeScript installation that runs this server to the response.
        """
        result = response['result']
        session = self.weaksession()
        if not isinstance(result, dict) or not session:
            return
        if package_json := self._get_typescript_package_json(session):
            result['packageJsonPath'] = str(package_json)

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
    LspTsgoPlugin.content_mapper_contributions = None


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


class LspTsgoSetContentMapperLocationCommand(sublime_plugin.WindowCommand):
    """Called by the packages that contribute content mappers, to report where the content mapper is installed."""

    def run(self, name: str, location: str) -> None:
        sublime.set_timeout_async(lambda: LspTsgoPlugin.set_content_mapper_location_async(name, location))


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
