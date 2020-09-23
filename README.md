# Munin Plugin Gallery

The plugin gallery generator retrieves [Munin](http://munin-monitoring.org) plugins from configured
sources.

The plugins are parsed and relevant meta data is extracted (programming language, capabilities,
graph categories).

The plugin data is used for generating a static website (via [hugo](https://gohugo.io)).

The default configuration supplied with this generator (see [config.yml](blob/master/config.yml))
is used for the [Munin Plugin Gallery](https://gallery.munin-monitoring.org/).


# Supported plugin source types

* git repository
* archive (e.g. tar.gz)
* local directory


# Hugo export

The configuration and layout of the exported website uses the content of the directory `hugo-base`.

See the [Hugo documentation](https://gohugo.io/documentation/) for details.


# Contribute

You are welcome to contribute to this plugin gallery generator in order to improve the plugin
parser or details of the generated website.

## Website layout

Adjust the content of the directory `hugo-base` and rebuild the local website:

```shell
./plugin-gallery-generator serve
```

After the first run you may want to omit the plugin collection in order to speed up the process:

```shell
./plugin-gallery-generator --skip-collect serve
```


## Plugin parser

Adjust the python-based [plugin-gallery-generator](blob/master/plugin-gallery-generator) and
rebuild the plugin tree:

```shell
./plugin-gallery-generator --skip-website
```

If you want to debug only a few (local) plugins, then you should specify the relevant local
directory in your configuration file (e.g. `config.yml`) in order to reduce processing time:

```yaml
sources:
  - name: foo
    type: directory
    location: your-local-example-directory
```

Afterwards you can inspect the content of `build/plugins/` and verify the resulting
data export.


# Dependencies

The file `munin-plugin-gallery-dependencies.equivs` can be used for generating a dummy
dependency deb package.  This simplifies the explicit documentation of all required
packages necessary for running the munin plugin generator.

Generate the dependency package:

```sh
equivs-build munin-plugin-gallery-dependencies.equivs
```
