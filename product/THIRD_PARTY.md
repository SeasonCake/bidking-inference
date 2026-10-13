# Dependencies and external inputs

The repository MIT license applies to the author-owned source in this directory.
It does not relicense third-party packages, the game, its assets or the operating system.

Dependencies are declared in `pyproject.toml`, `requirements*.txt` and the native crates'
`Cargo.toml`/`Cargo.lock`. Install them from their own distributions and retain their
notices when redistributing a build. This snapshot does not vendor their binaries or
source trees. Python, Tcl/Tk, NumPy, Pydantic, Streamlit, cryptographic libraries, OCR
runtimes, PyDivert/WinDivert and the Rust dependencies have their own terms.

Game tables and artwork, screenshots used as runtime assets, pet artwork, local model
outputs, runtime DLLs/drivers and compiler output are not part of this source export.
Existing package-data/asset manifests describe expected inputs; a referenced filename
or hash is not a license grant or an included file. Use data/assets you are entitled to
use and verify the applicable redistribution terms before distributing a full package.

The root `legacy/` snapshots and documentation screenshots retain the distinct notices
already stated in [NOTICE](../NOTICE.md). They are not current gameplay data or test
evidence for the present application. The author's production signing/data/activation
keys and commercial/member data are never build dependencies supplied by this repository.
