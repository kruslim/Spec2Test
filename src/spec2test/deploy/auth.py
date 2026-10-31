"""Who is signed in. The hosted app asks only this module, so the tests can sign someone in
without a real identity provider."""

from __future__ import annotations


def current_email() -> str | None:
    """The signed-in user's email, or None. An email the provider says is unverified does
    not count: anyone can type an unverified address."""
    import streamlit as st

    user = st.user
    if not user.is_logged_in:
        return None
    if user.get("email_verified") is False:
        return None
    email = user.get("email")
    return str(email) if email else None


def sign_in() -> None:
    import streamlit as st

    st.login()


def sign_out() -> None:
    import streamlit as st

    st.logout()
