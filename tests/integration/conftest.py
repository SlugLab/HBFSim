def pytest_addoption(parser):
    parser.addoption(
        "--exact-profile",
        action="store",
        default=None,
        help="run explicitly requested real SM120 exact workload tests",
    )
