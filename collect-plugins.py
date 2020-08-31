#!/usr/bin/env python3


import aiohttp
import asyncio
import asyncio.subprocess
import collections
import multiprocessing
import logging
import os
import re
import shutil
import sys
import tempfile


EXAMPLE_GRAPH_DIRECTORY_NAME = "example-graphs"


PluginSourceDescription = collections.namedtuple(
    "PluginSourceDescription", ("name", "archive_url", "archive_path"))


PLUGIN_SOURCES = (
    PluginSourceDescription(
        "munin-master",
        "https://github.com/munin-monitoring/munin/archive/master.tar.gz",
        "munin-master/plugins"),
    PluginSourceDescription(
        "munin-2.0",
        "https://github.com/munin-monitoring/munin/archive/stable-2.0.tar.gz",
        "munin-stable-2.0/plugins"),
    PluginSourceDescription(
        "munin-contrib",
        "https://github.com/munin-monitoring/contrib/archive/master.tar.gz",
        "contrib-master/plugins"),
)


class MuninPluginRepositoryProcessingError(IOError):
    """ any kind of error happened while processing a plugin source"""


class MuninPlugin:

    # special periods (day, week, month, year) and numbers are supported
    EXAMPLE_GRAPH_SUFFIX_REGEX = r"-(day|week|month|year|\d+).png"
    # the "stable-2.0" branch of the core repository uses a ".in" suffix for all plugin files
    OPTIONAL_PLUGIN_FILENAME_SUFFIXES = (".in",)
    FAMILY_REGEX = re.compile(r"^.*#%#\s*family\s*=\s*(\w+).*$", flags=re.MULTILINE)
    CAPABILITIES_REGEX = re.compile(
        r"^.*#%#\s*capabilities\s*=\s*(?:\s*(\w+))*.*$", flags=re.MULTILINE)
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
    # http://guide.munin-monitoring.org/en/latest/reference/graph-category.html#well-known-categories
    WELL_KNOWN_CATEGORIES = {
        "1sec", "antivirus", "appserver", "auth", "backup", "chat", "cloud", "cms", "cpu", "db",
        "devel", "disk", "dns", "filetransfer", "forum", "fs", "fw", "games", "htc",
        "loadbalancer", "mail", "mailinglist", "memory", "munin", "network", "other", "printing",
        "processes", "radio", "san", "search", "security", "sensors", "spamfilter", "streaming",
        "system", "time", "tv", "virtualization", "voip", "webserver", "wiki", "wireless",
    }

    def __init__(self, plugin_filename):
        self._plugin_filename = plugin_filename
        self.name = os.path.basename(plugin_filename)
        for suffix in self.OPTIONAL_PLUGIN_FILENAME_SUFFIXES:
            if self.name.endswith(suffix):
                self.name = self.name[:-len(suffix)]
        self._image_filenames = self._find_images()
        self._is_initialized = False

    def _find_images(self):
        example_graphs = {}
        example_graph_directory = os.path.join(
            os.path.dirname(self._plugin_filename), EXAMPLE_GRAPH_DIRECTORY_NAME)
        example_graph_filename_pattern = re.compile(self.name + self.EXAMPLE_GRAPH_SUFFIX_REGEX)
        try:
            graph_filenames = os.listdir(example_graph_directory)
        except OSError:
            graph_filenames = []
        for graph_filename in graph_filenames:
            match = example_graph_filename_pattern.match(graph_filename)
            if match:
                image_key = match.groups()[0]
                example_graphs[image_key] = graph_filename
        return example_graphs

    async def initialize(self):
        if not self._is_initialized:
            with open(self._plugin_filename, "r") as raw:
                self.plugin_code = raw.read()
            self.documentation = await self._parse_documentation()
            self.family = self._parse_family()
            self.capabilities = self._parse_family()
            self.categories = self._parse_categories()
            self._is_initialized = True

    async def _parse_documentation(self):
        """ parse the documentation and return a markdown formatted text """
        # quickly scan the file content in order to skip "perldoc" for files without documentation
        if "=head1" not in self.plugin_code:
            return None
        try:
            process = await asyncio.subprocess.create_subprocess_exec(
                *("perldoc", "-o", "markdown", "-F", "-T", self._plugin_filename),
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
        # TODO: add some post-processing
        return documentation

    def _parse_capabilities(self):
        capabilities_match = self.CAPABILITIES_REGEX.search(self.plugin_code)
        return tuple(capabilities_match.groups()) if capabilities_match else tuple()

    def _parse_family(self):
        family_match = self.FAMILY_REGEX.search(self.plugin_code)
        return family_match.groups()[0] if family_match else None

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

    def get_unexpected_categories(self):
        return self.categories.difference(self.WELL_KNOWN_CATEGORIES)

    def get_details(self):
        return {
            "documentation": bool(self.documentation),
            "family": self.family,
            "capabilities": self.capabilities,
            "categories": set(self.categories),
            "expected_categories": self.get_unexpected_categories(),
            "image_filenames": dict(self._image_filenames),
        }

    def __str__(self):
        if self._image_filenames:
            return "Plugin '{:s}' ({:d} example graphs)".format(
                self.name, len(self._image_filenames))
        else:
            return "Plugin '{:s}'".format(self.name)


class MuninPluginRepositorySource:

    def __init__(self, name, archive_url, path=None):
        self.name = name
        self._archive_url = archive_url
        self._archive_filter_path = path
        self._is_downloaded = False

    async def initialize(self):
        if not self._is_downloaded:
            self._extract_directory = tempfile.mkdtemp(prefix="munin-gallery-")
            self._plugins_directory = await self._import_archive(
                self._extract_directory, self._archive_url, path=self._archive_filter_path)
            self._is_downloaded = True

    def __del__(self):
        if self._extract_directory:
            shutil.rmtree(self._extract_directory, ignore_errors=True)
            self._extract_directory = None

    @staticmethod
    async def _import_archive(target_directory, archive_url, path=None):
        if path.rstrip(os.path.sep) == os.path.curdir:
            path = None
        extract_command = ["tar", "--extract", "--gzip", "--directory", target_directory]
        if path is not None:
            extract_command.append(path)
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
                "Failed to extract source archive ({}): {}".format(archive_url, process.stdout))

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
                if status.st_mode & 0o100 > 0:
                    # the file is executable - we can assume, that it is a plugin
                    yield MuninPlugin(full_path)


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


async def import_plugins():
    initialized_plugins = asyncio.Queue()
    pending_plugins = asyncio.Queue()
    plugin_sources = [
        MuninPluginRepositorySource(
            source_description.name,
            source_description.archive_url,
            source_description.archive_path)
        for source_description in PLUGIN_SOURCES]
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
    statistics = {"all": [], "missing_doc": [], "missing_family": [], "missing_capabilities": [], "unexpected_categories": []}
    all_plugins = []
    while not initialized_plugins.empty():
        all_plugins.append(await initialized_plugins.get())
    for plugin in all_plugins:
        statistics["all"].append(plugin)
        if not plugin.documentation:
            statistics["missing_doc"].append(plugin)
        if not plugin.family:
            statistics["missing_family"].append(plugin)
        if not plugin.capabilities:
            statistics["missing_capabilities"].append(plugin)
        if plugin.get_unexpected_categories():
            statistics["unexpected_categories"].append(plugin)
    for key, matches in statistics.items():
        print("{}: {:d}".format(key, len(matches)))


async def import_local_plugins(plugin_filenames):
    for plugin_filename in plugin_filenames:
        plugin = MuninPlugin(plugin_filename)
        await plugin.initialize()
        print(plugin.get_details())


def main():
    if len(sys.argv) > 1:
        asyncio.run(import_local_plugins(sys.argv[1:]))
    else:
        asyncio.run(import_plugins())


if __name__ == "__main__":
    main()
