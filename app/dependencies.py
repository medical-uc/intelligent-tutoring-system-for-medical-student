from functools import lru_cache

from neo4j import Driver

from src.student_kg.driver import ensure_constraints, make_driver


@lru_cache(maxsize=1)
def _driver() -> Driver:
    driver = make_driver()
    ensure_constraints(driver)
    return driver


def get_driver() -> Driver:
    return _driver()
