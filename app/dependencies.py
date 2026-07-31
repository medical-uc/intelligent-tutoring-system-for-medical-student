from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from neo4j import Driver

from src.student_kg.driver import ensure_constraints, make_driver
from src.student_kg.session import validate_session

_bearer_scheme = HTTPBearer()


@lru_cache(maxsize=1)
def _driver() -> Driver:
    driver = make_driver()
    ensure_constraints(driver)
    return driver


def get_driver() -> Driver:
    return _driver()


def get_current_student_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    driver: Driver = Depends(get_driver),
) -> str:
    student_id = validate_session(driver, credentials.credentials)
    if student_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid, expired, or revoked session")
    return student_id
