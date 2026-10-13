try:
    from _runtime_component import main
except ImportError:
    from runtime_component import main

raise SystemExit(main())
