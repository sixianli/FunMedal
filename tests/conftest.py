from bridge_harness import lock_out_resident


def pytest_sessionstart(session):
    lock_out_resident()
