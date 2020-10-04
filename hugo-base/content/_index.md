## Munin Plugin Gallery

This is the place where you can browse descriptions and graph images for [Munin](https://munin-monitoring.org) plugins.


### Plugin Sources

The gallery contains all plugins from the following sources:
* [munin-master](/repositories/munin/): the `master` branch of the Munin repository represents the state of the (to be released) version 3.x of Munin
* [munin-2.0](/repositories/munin-2.0/): the `stable-2.0` branch of the Munin repository represents the state of the current stable release version of Munin (v2.0.x)
* [contrib](/repositories/munin-contrib/): the `contrib` repository contains plugins contributed by a broad community and maintained by the Munin developers

You are welcome to [suggest](/contribute/) additional source repositories.


### Finding a plugin

* type a name or keyword into the search field
* browse through the plugin attributes (in the left sidebar)
* go through the long list of tags (in the top navigation bar)


### Contribute

You are very welcome to improve the documentation of the existing plugins (in *perldoc* format) and provide additional example graph images to the `contrib` repository. The more descriptive content is there, the more helpful the Plugin Gallery is for all users.

Read [Contribute](/contribute/) for more details.


### Graph Categories

To get a clear and user-friendly overview in the plugin gallery (and on Munin WebGUI), we work on reducing the number of categories. Have a look at our list of [well-known-categories](http://guide.munin-monitoring.org/en/latest/reference/graph-category.html?highlight=gallery#well-known-categories), and choose an appropriate category to present your plugin in the gallery.

Users can always change the category to adapt their personal view by changing the plugins sourcecode before distributing it on their servers or by configuration setting on the Munin masters side (`munin.conf`).
