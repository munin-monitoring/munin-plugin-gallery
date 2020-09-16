#!/usr/bin/env python3


import aiohttp
import asyncio
import asyncio.subprocess
import collections
import datetime
import enum
import multiprocessing
import logging
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
import yaml


EXAMPLE_GRAPH_DIRECTORY_NAME = "example-graphs"


class RepositorySourceType(enum.Enum):
    GIT = "git"
    ARCHIVE = "archive"


PluginSourceDescription = collections.namedtuple(
    "PluginSourceDescription", ("name", "source_type", "source_url", "branch", "path"))


PLUGIN_SOURCES = (
    PluginSourceDescription(
        "munin",
        RepositorySourceType.GIT,
        "https://github.com/munin-monitoring/munin.git",
        "master",
        "plugins"),
    PluginSourceDescription(
        "munin-2.0",
        RepositorySourceType.GIT,
        "https://github.com/munin-monitoring/munin.git",
        "stable-2.0",
        "plugins"),
    PluginSourceDescription(
        "munin-contrib",
        RepositorySourceType.GIT,
        "https://github.com/munin-monitoring/contrib.git",
        "master",
        "plugins"),
)


class MuninPluginExampleGraph(collections.namedtuple("MuninPluginExampleGraph", "key filename")):

    def _get_sort_key(self):
        """ calculate a sorting weight based on the "key"

        The special keys for daily, weekly, monthly and yearly graphs are supposed to appear first.
        Numeric keys follow.
        All other keys are used as sorting keys without further processing.
        """
        try:
            return ({"day": -4, "weeky": -3, "month": -2, "year": -1}[self.key.lower()], "")
        except KeyError:
            pass
        try:
            return (int(self.key), "")
        except ValueError:
            pass
        return (100, self.key)

    def __lt__(self, other):
        return self._get_sort_key() < other._get_sort_key()


class MuninPluginRepositoryProcessingError(IOError):
    """ any kind of error happened while processing a plugin source"""


class MuninPlugin:

    # special periods (day, week, month, year) and numbers are supported
    EXAMPLE_GRAPH_SUFFIX_REGEX = r"-(day|week|month|year|\d+).png"
    # the "stable-2.0" branch of the core repository uses a ".in" suffix for all plugin files
    OPTIONAL_PLUGIN_FILENAME_SUFFIXES = (".in",)
    FAMILY_REGEX = re.compile(r"^.*#%#\s*family\s*=\s*(.+)$")
    CAPABILITIES_REGEX = re.compile(r"^.*#%#\s*capabilities\s*=\s*(.+)$")
    # Most plugins contain a description in the first few lines ("NAME - SUMMARY ...").
    # Some irrelevant tokens (e.g. the prefix "Munin Plugin to" or a trailing dot) are ignored.
    SUMMARY_REGEX = re.compile(
        r"^\w+\s+-\s+(Munin )?((Plugin|Script) )?(to )?(?P<summary>.*?)\.?$", flags=re.IGNORECASE)
    CATEGORY_LINE_BLACKLIST_REGEXES = (
        re.compile(r"(?:label|documentation|\bthe\b|filterwarnings)"),
        # ignore existing ambiguous word combinations
        re.compile(r"(?:env\.category|/category/|category queries|category\.|force_category)"),
        # ignore SQL expressions
        re.compile(r"select.*from.*(?:join|where)"),
        # ignore any kind of comments
        re.compile(r"^\s*(?:#|//|/\*)"),
        # no variable may be part of the category name
        re.compile(r"category.*[&\$]"),
    )
    CATEGORY_REGEX = re.compile(
        r"^(?P<line>.*[^$.]category[^\w\n]+(?P<category>\w+).*)$", flags=re.MULTILINE)
    KEYWORDS_REMOVAL_REGEXES = (
        # the munin repository groups plugins by operating system
        re.compile(r"^node\.d\."),
        # omit the platform-independent plugin directory name used in the munin repository
        re.compile(r"^node\.d$"),
    )
    # http://guide.munin-monitoring.org/en/latest/reference/graph-category.html#well-known-categories
    WELL_KNOWN_CATEGORIES = {
        "1sec", "antivirus", "appserver", "auth", "backup", "chat", "cloud", "cms", "cpu", "db",
        "devel", "disk", "dns", "filetransfer", "forum", "fs", "fw", "games", "htc",
        "loadbalancer", "mail", "mailinglist", "memory", "munin", "network", "other", "printing",
        "processes", "radio", "san", "search", "security", "sensors", "spamfilter", "streaming",
        "system", "time", "tv", "virtualization", "voip", "webserver", "wiki", "wireless",
    }
    # the list of mappings is ordered
    IMPLEMENTATION_LANGUAGE_REGEXES = {
        "bash": re.compile(r"\Wbash(\W|$)"),
        "ksh": re.compile(r"\Wksh(\W|$)"),
        "perl": re.compile(r"\Wperl(\W|$)"),
        "php": re.compile(r"\Wphp"),
        "python2": re.compile(r"\Wpython2?(\W|$)"),
        "python3": re.compile(r"\Wpython3"),
        "ruby": re.compile(r"\Wruby"),
        "sh": re.compile(r"\Wsh(\W|$)"),
        "zsh": re.compile(r"\Wzsh(\W|$)"),
    }
    HEADING_REGEX = re.compile(r"^(#+.*)$", flags=re.MULTILINE)
    CAPITALIZATION_UPPER = {"IP", "TCP", "UDP"}
    CAPITALIZATION_LOWER = {"a", "the", "in", "for", "to", "and"}

    def __init__(self, plugin_filename, repository_source=None, name=None, language=None):
        self.plugin_filename = plugin_filename
        self.repository_source = repository_source
        self.name = os.path.basename(plugin_filename) if name is None else name
        self.implementation_language = language
        for suffix in self.OPTIONAL_PLUGIN_FILENAME_SUFFIXES:
            if self.name.endswith(suffix):
                self.name = self.name[:-len(suffix)]
        self.example_graphs = self._find_images()
        self._is_initialized = False

    def _find_images(self):
        example_graphs = []
        example_graph_directory = os.path.join(
            os.path.dirname(self.plugin_filename), EXAMPLE_GRAPH_DIRECTORY_NAME)
        example_graph_filename_pattern = re.compile(self.name + self.EXAMPLE_GRAPH_SUFFIX_REGEX)
        try:
            graph_filenames = os.listdir(example_graph_directory)
        except OSError:
            graph_filenames = []
        for graph_filename in graph_filenames:
            match = example_graph_filename_pattern.match(graph_filename)
            if match:
                image_key = match.groups()[0]
                example_graphs.append(MuninPluginExampleGraph(
                    image_key, os.path.join(example_graph_directory, graph_filename)))
        example_graphs.sort()
        return example_graphs

    async def initialize(self):
        if not self._is_initialized:
            with open(self.plugin_filename, "rb") as raw:
                self.plugin_code = raw.read().decode(errors="ignore")
            self.documentation = await self._parse_documentation()
            self.family = self._parse_family()
            self.capabilities = self._parse_capabilities()
            self.categories = self._parse_categories()
            if self.repository_source:
                self.changed_timestamp = await self.repository_source.get_file_timestamp(
                    self.plugin_filename)
            else:
                self.changed_timestamp = None
            self.path_keywords = tuple(self._get_keywords())
            self.summary = self._guess_summary()
            if self.implementation_language is None:
                self.implementation_language = self._parse_implementation_language()
            self._is_initialized = True

    def _get_keywords(self):
        if self.repository_source:
            relative_path = self.repository_source.get_relative_path(
                os.path.dirname(self.plugin_filename))
        else:
            relative_path = ""
        for token in relative_path.lower().split(os.path.sep):
            for regex in self.KEYWORDS_REMOVAL_REGEXES:
                token = regex.sub("", token)
            if token:
                yield token

    def _guess_summary(self):
        # we expect the summary within the first few lines of the documentation
        if not self.documentation:
            return None
        for line in self.documentation.splitlines()[:8]:
            match = self.SUMMARY_REGEX.search(line)
            if match:
                return match.groupdict()["summary"]
        else:
            return None

    @classmethod
    def _rewrite_match_capitalization(cls, match):
        """ downgrade the capitalization of each word of the match """
        result = []
        for token in match.groups()[0].split():
            if token.upper() in cls.CAPITALIZATION_UPPER:
                # upper case for specific words (e.g. "IP")
                token = token.upper()
            elif token.lower() in cls.CAPITALIZATION_LOWER:
                # lower case for all trivial words
                token = token.lower()
            else:
                # capitalize only the first letter for all other words
                token = token.title()
            result.append(token)
        return " ".join(result)

    async def _parse_documentation(self):
        """ parse the documentation and return a markdown formatted text """
        # quickly scan the file content in order to skip "perldoc" for files without documentation
        if "=head1" not in self.plugin_code:
            return None
        try:
            process = await asyncio.subprocess.create_subprocess_exec(
                *("perldoc", "-o", "markdown", "-F", "-T", self.plugin_filename),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            logging.warning("Failed to execute perldoc: {}".format(exc))
            return None
        await process.wait()
        if process.returncode != 0:
            logging.info("Failed to generate documentation for plugin '{}'.".format(self.name))
            return None
        documentation = (await process.stdout.read()).decode()
        # remove empty lines and whitespace and the beginning and end
        documentation = documentation.strip()
        # fix all-uppercase style (e.g. "NAME" -> "Name")
        documentation = self.HEADING_REGEX.sub(self._rewrite_match_capitalization, documentation)
        # reduce the level of all headings (the template applies level 1 to the plugin title)
        documentation = re.sub(r"^#", "##", documentation, flags=re.MULTILINE)
        # TODO: add some post-processing
        return documentation

    def _parse_capabilities(self):
        for line in self.plugin_code.splitlines():
            match = self.CAPABILITIES_REGEX.search(line)
            if match:
                return tuple(match.groups()[0].strip().lower().split())
        else:
            return None

    def _parse_family(self):
        for line in self.plugin_code.splitlines():
            match = self.FAMILY_REGEX.search(line)
            if match:
                return match.groups()[0].strip().lower()
        else:
            return None

    def _parse_categories(self):
        categories = set()
        for line, category in self.CATEGORY_REGEX.findall(self.plugin_code):
            if len(line.splitlines()) != 1:
                continue
            if any(blacklist_regex.search(line)
                   for blacklist_regex in self.CATEGORY_LINE_BLACKLIST_REGEXES):
                continue
            categories.add(category.lower())
        return categories

    def _parse_implementation_language(self):
        first_line = self.plugin_code.splitlines()[0]
        for name, regex in self.IMPLEMENTATION_LANGUAGE_REGEXES.items():
            if regex.search(first_line):
                return name
        else:
            return None

    def get_unexpected_categories(self):
        return self.categories.difference(self.WELL_KNOWN_CATEGORIES)

    def get_details(self):
        return {
            "documentation": bool(self.documentation),
            "family": self.family,
            "capabilities": self.capabilities,
            "categories": set(self.categories),
            "keywords": set(self.path_keywords),
            "unexpected_categories": self.get_unexpected_categories(),
            "image_filenames": dict(self._image_filenames),
            "changed_timestamp": self.changed_timestamp,
        }

    def __str__(self):
        if self._image_filenames:
            return "Plugin '{:s}' ({:d} example graphs)".format(
                self.name, len(self._image_filenames))
        else:
            return "Plugin '{:s}'".format(self.name)


class MuninPluginRepository:

    def __init__(self, source):
        self.name = source.name
        self._source_type = source.source_type
        self._source_url = source.source_url
        self._branch = source.branch
        self._filter_path = source.path
        self._is_downloaded = False

    async def initialize(self):
        if not self._is_downloaded:
            self._extract_directory = tempfile.mkdtemp(prefix="munin-gallery-")
            if self._source_type == RepositorySourceType.GIT:
                self._plugins_directory = await self._import_git_repository(
                    self._extract_directory, self._source_url, self._branch,
                    path=self._filter_path)
            elif self._source_type == RepositorySourceType.ARCHIVE:
                self._plugins_directory = await self._import_archive(
                    self._extract_directory, self._source_url, path=self._filter_path)
            else:
                raise ValueError("Invalid source type: {}".format(self._source_type))
            self._is_downloaded = True

    def __del__(self):
        if self._is_downloaded:
            shutil.rmtree(self._extract_directory, ignore_errors=True)
            del self._extract_directory
            del self._plugins_directory
            self._is_downloaded = False

    async def _get_git_file_timestamp(self, filename):
        """ retrieve the timestamp of the most recent commit affecting the filename """
        dirname, basename = os.path.dirname(filename), os.path.basename(filename)
        try:
            process = await asyncio.subprocess.create_subprocess_exec(
                *("git", "log", "-n", "1", "--no-merges", "--format=format:%aI", "--", basename),
                cwd=dirname, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError:
            logging.warning(
                "Failed to run 'git log' while retrieving the timestamp of '{}'.".format(filename))
            return None
        timestamp_raw, error_output = await process.communicate()
        if process.returncode != 0:
            logging.warning(
                "Failed to retrieve the timestamp of '{}' via 'git log': {}"
                .format(filename, error_output.decode()))
            return None
        try:
            return datetime.datetime.fromisoformat(timestamp_raw.decode())
        except ValueError:
            logging.warning(
                "Failed to parse file timestamp of '{}': {}".format(filename, timestamp_raw))
            return None

    async def get_file_timestamp(self, filename):
        if self._source_type == RepositorySourceType.GIT:
            return await self._get_git_file_timestamp(filename)
        elif self._source_type == RepositorySourceType.ARCHIVE:
            # github's tar archive does not support proper file timestamps
            return None
        else:
            raise ValueError("Invalid source type: {}".format(self._source_type))

    @staticmethod
    async def _import_git_repository(target_directory, repository_url, branch, path=None):
        if path.rstrip(os.path.sep) == os.path.curdir:
            path = None
        try:
            # we cannot use "--depth=1", since we are interested in the file timestamps
            process = await asyncio.subprocess.create_subprocess_exec(
                *("git", "clone", "--single-branch", "--branch", branch,
                  repository_url, target_directory),
                stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise MuninPluginRepositoryProcessingError(
                "Failed to spawn process for repository retrieval (git): {}" .format(exc))
        await process.wait()
        if process.returncode == 0:
            return os.path.join(target_directory, path) if path else target_directory
        else:
            raise MuninPluginRepositoryProcessingError(
                "Failed to extract source archive ({}): {}"
                .format(repository_url, (await process.stderr.read()).decode()))

    @staticmethod
    async def _import_archive(target_directory, archive_url, path=None):
        if path.rstrip(os.path.sep) == os.path.curdir:
            path = None
        # Strip the top-level path before extracting. Github assembles the name of this path
        # component by combining the repository name and the branch name.
        extract_command = [
            "tar", "--extract", "--gzip", "--strip-components=1", "--directory", target_directory]
        if path:
            # extract the specified path and ignore the top-level directory of github's archive
            extract_command.extend(["--wildcards", os.path.join("*", path)])
        try:
            process = await asyncio.subprocess.create_subprocess_exec(
                *extract_command, stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise MuninPluginRepositoryProcessingError(
                "Failed to spawn process for archival extraction (tar): {}" .format(exc))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(archive_url) as response:
                    while True:
                        chunk = await response.content.read(256 * 1024)
                        if chunk:
                            process.stdin.write(chunk)
                        else:
                            break
                    process.stdin.close()
        except IOError as exc:
            raise MuninPluginRepositoryProcessingError(
                "Failed to download source archive from '{}': {}'".format(archive_url, exc))
        await process.wait()
        if process.returncode == 0:
            return os.path.join(target_directory, path if path else os.path.curdir)
        else:
            raise MuninPluginRepositoryProcessingError(
                "Failed to extract source archive ({}): {}"
                .format(archive_url, (await process.stderr.read()).decode()))

    async def get_plugins(self):
        await self.initialize()
        for dirpath, dirnames, filenames in os.walk(self._plugins_directory):
            if os.path.basename(dirpath) in {EXAMPLE_GRAPH_DIRECTORY_NAME, "node.d.debug"}:
                # example graph directories are not expected to contain plugins
                continue
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                try:
                    status = os.stat(full_path, follow_symlinks=False)
                except OSError:
                    pass
                # every executable file is assumed to be a plugin
                if status.st_mode & 0o100 > 0:
                    yield MuninPlugin(full_path, self)
                elif filename.endswith(".in"):
                    # the plugin files in the stable-2.0 repository are not executable
                    yield MuninPlugin(
                        full_path, repository_source=self, name=filename[:-3])
                elif filename.endswith(".c"):
                    yield MuninPlugin(
                        full_path, repository_source=self, name=filename[:-2], language="c")
                elif filename.endswith(".cpp"):
                    yield MuninPlugin(
                        full_path, repository_source=self, name=filename[:-4], language="cpp")
                else:
                    # this file is probably not a plugin
                    pass

    def get_relative_path(self, path):
        return str(pathlib.Path(path).relative_to(self._plugins_directory))


class ContentIndexer:
    """ a trivial content indexer for reducing the given text to a minimal set of words

    Specific lines, special characters and superfluous whitespace is removed.
    """

    # ignore lines with headings and magic markers
    IGNORE_LINE_REGEX = re.compile(r"(^#|^\s+#%#)")
    REMOVAL_REGEXES = (
        # remove common (very unspecific) words
        re.compile(r"\b(copyright|munin|plugin)\b", flags=re.IGNORECASE),
        # remove all special characters
        re.compile(r"[^\w\s.]"),
    )
    MERGE_WHITESPACE_REGEX = re.compile(r"\s+")

    @classmethod
    def get_indexing_content(cls, text):
        result = []
        for line in text.splitlines():
            if cls.IGNORE_LINE_REGEX.search(line):
                continue
            for regex in cls.REMOVAL_REGEXES:
                line = regex.sub(" ", line)
            line = cls.MERGE_WHITESPACE_REGEX.sub(" ", line)
            line = line.strip()
            if line:
                result.append(line)
        return " ".join(result)


class MuninPluginsHugoExport:

    MISSING_DOCUMENTATION_TEXT = "Sadly there is no documentation for this plugin"

    def __init__(self, hugo_directory):
        self._hugo_directory = hugo_directory
        self._content_directory = os.path.join(self._hugo_directory, "content")
        self._plugins_directory = os.path.join(self._content_directory, "plugins")
        self._plugins = []

    async def build(self):
        try:
            process = await asyncio.subprocess.create_subprocess_exec(
                "hugo", cwd=self._hugo_directory,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            logging.error("Failed to run 'hugo': {}".format(exc))
            return False
        await process.wait()
        if process.returncode == 0:
            return True
        else:
            logging.error(
                "Failed to build hugo site: {}".format((await process.stderr.read()).decode()))
            return False

    def get_hugo_frontmatter(self, plugin):
        result = {
            "title": plugin.name,
        }
        if plugin.repository_source:
            result["repositories"] = [plugin.repository_source.name]
        if plugin.documentation:
            result["documentation_status"] = ["documented"]
        else:
            result["documentation_status"] = ["missing documentation"]
        if plugin.changed_timestamp:
            result["date"] = plugin.changed_timestamp.isoformat(timespec="seconds")
        if plugin.summary:
            result["summary"] = plugin.summary
        if plugin.categories:
            result["categories"] = tuple(plugin.categories)
        if plugin.family:
            result["families"] = [plugin.family]
        if plugin.capabilities:
            result["capabilities"] = tuple(plugin.capabilities)
        if plugin.path_keywords:
            result["keywords"] = tuple(sorted(plugin.path_keywords))
        if plugin.implementation_language:
            result["implementation_languages"] = [plugin.implementation_language]
        if plugin.documentation:
            result["indexing_content"] = ContentIndexer.get_indexing_content(plugin.documentation)
        return result

    @staticmethod
    def _set_timestamp_of_plugin(path, plugin):
        if plugin.changed_timestamp:
            os.utime(path, tuple(2 * [int(plugin.changed_timestamp.timestamp())]))

    async def add(self, plugin):
        plugin_directory = os.path.join(self._plugins_directory, plugin.name)
        try:
            os.makedirs(plugin_directory, exist_ok=True)
        except OSError as exc:
            logging.warning(
                "Failed to create hugo plugin directory ({}): {}".format(plugin_directory, exc))
            return False
        documentation = plugin.documentation or self.MISSING_DOCUMENTATION_TEXT
        source_path = os.path.join(plugin_directory, "source")
        shutil.copy(plugin.plugin_filename, source_path)
        self._set_timestamp_of_plugin(source_path, plugin)
        local_graphs = []
        for graph in plugin.example_graphs:
            destination = os.path.join(
                plugin_directory, graph.key + os.path.splitext(graph.filename)[1])
            shutil.copy(graph.filename, destination)
            self._set_timestamp_of_plugin(destination, plugin)
            local_graphs.append({
                "key": graph.key,
                "path": os.path.basename(destination),
            })
        export_filename = os.path.join(plugin_directory, "index.md")
        with open(export_filename, "w") as plugin_file:
            plugin_file.write("---" + os.linesep)
            meta_data = self.get_hugo_frontmatter(plugin)
            if local_graphs:
                meta_data["example_graphs"] = local_graphs
            # convert tuples to lists - for basic type dumps in yaml
            for key in meta_data:
                if isinstance(meta_data[key], tuple):
                    meta_data[key] = list(meta_data[key])
            plugin_file.write(yaml.dump(meta_data, indent=4))
            plugin_file.write("---" + os.linesep)
            plugin_file.write(documentation + os.linesep)
            show_source_format_string = """
{{< collapse title="Source Code" >}}
{{< code lang="%(language)s" file="/%(path)s" >}}
{{< /collapse >}}
"""
            plugin_file.write(show_source_format_string % {
                "language": plugin.implementation_language or "",
                "path": pathlib.Path(source_path).relative_to(self._content_directory),
            })
        self._set_timestamp_of_plugin(export_filename, plugin)
        self._set_timestamp_of_plugin(plugin_directory, plugin)
        self._plugins.append(plugin)

    def get_statistics(self):
        return {
            "all": len(self._plugins),
            "missing_documentation": len([p for p in self._plugins if not p.documentation]),
            "missing_family": len([p for p in self._plugins if not p.family]),
            "missing_capabilities": len([p for p in self._plugins if not p.capabilities]),
            "missing_summary": len([p for p in self._plugins if not p.summary]),
            # TODO: evaluate
            "unexpected_categories": len([p for p in self._plugins
                                          if p.get_unexpected_categories()]),
        }


async def synchronize_directories(src, dest, exclude_patterns=None):
    rsync_arguments = ["rsync", "--archive", "--delete"]
    for exclude_pattern in (exclude_patterns or []):
        rsync_arguments.extend(["--exclude", exclude_pattern])
    rsync_arguments.append(src.rstrip(os.path.sep) + os.path.sep)
    rsync_arguments.append(dest.rstrip(os.path.sep) + os.path.sep)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as exc:
        logging.error(
            "Failed to create synchronization destination ({}): {}".format(dest, exc))
        return False
    try:
        process = await asyncio.subprocess.create_subprocess_exec(
            *rsync_arguments, stderr=asyncio.subprocess.PIPE)
    except OSError as exc:
        logging.error("Failed to execute rsync: {}".format(exc))
        return False
    _, error_output = await process.communicate()
    if process.returncode == 0:
        return True
    else:
        logging.error("Failed to copy directory from '{}' to '{}': {}"
                      .format(src, dest, " ".join(error_output.decode().splitlines())))
        return False


async def import_plugin_source_archive(plugin_source, plugin_queue):
    try:
        async for plugin in plugin_source.get_plugins():
            logging.info("Adding plugin '{}'".format(plugin.name))
            await plugin_queue.put(plugin)
    except Exception as exc:
        logging.error("Failed to import plugin source archive ({}): {}"
                      .format(plugin_source.name, exc))


async def worker_initialize_plugins(jobs, destination):
    while True:
        plugin = await jobs.get()
        try:
            await plugin.initialize()
        except Exception as exc:
            logging.warning("Failed to initialize plugin ({}): {}".format(plugin.name, exc))
            import traceback
            logging.warning(traceback.format_exc())
        else:
            pending_count = jobs.qsize()
            done_count = destination.qsize()
            logging.info("[{:d}/{:d}] Plugin '{}' finished"
                         .format(done_count, pending_count + done_count, plugin.name))
            await destination.put(plugin)
        finally:
            jobs.task_done()


async def worker_export_plugins_to_hugo(exporter, input_queue):
    while True:
        new_plugin = await input_queue.get()
        try:
            await exporter.add(new_plugin)
        except Exception as exc:
            logging.error("Failed to add plugin to exporter: {}".format(exc))
        input_queue.task_done()


async def import_plugins(initialized_plugins):
    pending_plugins = asyncio.Queue()
    plugin_sources = [MuninPluginRepository(source) for source in PLUGIN_SOURCES]
    plugin_source_workers = []
    for source in plugin_sources:
        task = asyncio.create_task(import_plugin_source_archive(source, pending_plugins))
        plugin_source_workers.append(task)
    plugin_workers = []
    for _ in range(multiprocessing.cpu_count()):
        task = asyncio.create_task(worker_initialize_plugins(pending_plugins, initialized_plugins))
        plugin_workers.append(task)
    await asyncio.gather(*plugin_source_workers, return_exceptions=True)
    await pending_plugins.join()


def get_plugin_statistics(plugins):
    statistics = {
        "all": [],
        "missing_documentation": [],
        "missing_family": [],
        "missing_capabilities": [],
        "missing_summary": [],
        "unknown_implementation_language": [],
        "unexpected_categories": [],
    }
    for plugin in plugins:
        statistics["all"].append(plugin)
        if not plugin.documentation:
            statistics["missing_documentation"].append(plugin)
        if not plugin.family:
            statistics["missing_family"].append(plugin)
        if not plugin.capabilities:
            statistics["missing_capabilities"].append(plugin)
        if not plugin.summary:
            statistics["missing_summary"].append(plugin)
        if not plugin.implementation_language:
            statistics["unknown_implementation_language"].append(plugin)
        if plugin.get_unexpected_categories():
            statistics["unexpected_categories"].append(plugin)
    return statistics


async def transfer_queue_to_list(input_queue):
    result = []
    while not input_queue.empty():
        result.append(await input_queue.get())
    return result


async def import_local_plugins(plugin_filenames):
    for plugin_filename in plugin_filenames:
        plugin = MuninPlugin(plugin_filename)
        await plugin.initialize()
        print(plugin.get_details())


async def publish_plugins_hugo(source, destination):
    if not await synchronize_directories(source, destination,
                                         [os.path.join("themes", "*", ".git") + os.path.sep]):
        logging.error("Failed to initialize hugo export directory: {}".format(destination))
        return False
    export = MuninPluginsHugoExport(destination)
    loaded_plugins = asyncio.Queue()
    worker = asyncio.create_task(worker_export_plugins_to_hugo(export, loaded_plugins))
    await import_plugins(loaded_plugins)
    worker.cancel()
    if not await export.build():
        logging.error("Failed to build the static export")
        return False
    for key, value in export.get_statistics().items():
        print("{}: {}".format(key, value))


def main():
    if True:
        base_dir = os.path.dirname(__file__)
        hugo_base_dir = os.path.join(base_dir, "hugo-base")
        hugo_build_dir = os.path.join(base_dir, "build", "hugo")
        asyncio.run(publish_plugins_hugo(hugo_base_dir, hugo_build_dir))
    elif len(sys.argv) > 1:
        asyncio.run(import_local_plugins(sys.argv[1:]))
    else:
        # only import
        loaded_plugins = asyncio.Queue()
        asyncio.run(import_plugins(loaded_plugins))
        plugins = asyncio.run(transfer_queue_to_list(loaded_plugins))
        for key, matches in get_plugin_statistics(plugins).items():
            print("{}: {:d}".format(key, len(matches)))


if __name__ == "__main__":
    main()
