---
title: "License"
---

## Plugins without License

Each plugin should contain a clear license statement.

The license is parsed from the plugin code.

An unambiguous approach for specifying a license is by using an [SPDX](https://spdx.org/) license
identifier.

The following example declares the license of a plugin both with a human readable description and
a corresponding SPDX identifier:

```pod
=head1 LICENSE

GNU General Public License v3.0 or later

SPDX-License-Identifier: GPL-3.0-or-later
```

The following plugins seem to lack a clear license statement.
Or maybe the generator code for this plugin gallery just did not parse the license properly.
This can be especially difficult, if the license is specified in free-form text and lacks specific
keywords (as it is common for MIT or BSD licenses).

You are encouraged to improve this by reporting an issue or fixing the issue on your own.
Please take a look at [how to contribute](/contribute/) for details.
