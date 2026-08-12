# Universe Public Product Nodes

`projects/` contains Product Nodes whose source is intentionally public. A
Node may be built and packaged independently while using the Universe control
plane.

Each Node records the immutable upstream import in `universe-node.json`.
The imported product source must not contain a local `.ai/` Workspace or a
legacy `.universe/` binding. Universe creates those only when the Node is
installed or attached on a machine.

There are currently no tracked public Product Node sources. Private Product
Nodes are registered in Universe but are not mirrored in this repository.
Their shared Runtime is delivered through the managed distribution boundary
rather than a tracked public source path.
