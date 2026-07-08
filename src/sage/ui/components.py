"""Reusable UI components for Sage dashboard."""

import streamlit as st
from typing import Literal


def metric_card(
    label: str,
    value: str | int | float,
    delta: str | None = None,
    delta_type: Literal["positive", "negative", "neutral"] = "neutral",
):
    """Render a metric card with elevation and hover state.

    Args:
        label: Uppercase label text (e.g., "TASKS COMPLETED")
        value: The metric value (number or string)
        delta: Optional change indicator (e.g., "+12%", "-3")
        delta_type: Color coding for delta (positive=green, negative=red)
    """
    delta_html = ""
    if delta:
        delta_html = f'<div class="metric-delta {delta_type}">{delta}</div>'

    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-value">{value}</div>
            <div class="metric-label">{label}</div>
            {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_chip(
    label: str,
    status: Literal["connected", "simulated", "disconnected"],
):
    """Render a status indicator chip with colored dot.

    Args:
        label: Status text (e.g., "Qwen API", "MCP Server")
        status: Connection state (connected=green, simulated=yellow, disconnected=red)
    """
    st.markdown(
        f"""
        <div class="status-chip">
            <div class="status-dot {status}"></div>
            <span>{label}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def empty_state(
    icon: str,
    title: str,
    description: str,
    action_label: str | None = None,
    action_key: str | None = None,
):
    """Render a guided empty state with icon, description, and optional action.

    Args:
        icon: Emoji or symbol (e.g., "📋", "🔍", "⚡")
        title: Main heading (e.g., "No rules learned yet")
        description: Guidance text explaining what to do
        action_label: Optional button text (e.g., "Go to Interactive")
        action_key: Streamlit button key (required if action_label provided)
    """
    st.markdown(
        f"""
        <div class="empty-state">
            <div class="empty-state-icon">{icon}</div>
            <div class="empty-state-title">{title}</div>
            <div class="empty-state-description">{description}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if action_label and action_key:
        st.button(action_label, key=action_key, type="primary")


def rule_card(
    rule_id: str,
    text: str,
    confidence: float,
    times_applied: int = 0,
    utility: float = 0.0,
    source: str = "unknown",
    pinned: bool = False,
):
    """Render a rule card with metadata and visual hierarchy.

    Args:
        rule_id: Unique identifier (e.g., "rule_abc123")
        text: The rule text
        confidence: Confidence score [0, 1]
        times_applied: Number of times rule was applied
        utility: Utility score (can be negative)
        source: Origin of the rule (e.g., "correction", "manual")
        pinned: Whether the rule is pinned (protected from decay)
    """
    pin_indicator = "📌 " if pinned else ""
    utility_sign = "+" if utility >= 0 else ""

    st.markdown(
        f"""
        <div class="rule-card">
            <div class="rule-card-header">
                <div class="rule-card-id">{pin_indicator}{rule_id}</div>
                <div class="rule-card-confidence">{confidence:.2f}</div>
            </div>
            <div class="rule-card-text">{text}</div>
            <div class="rule-card-meta">
                <div class="rule-card-meta-item">
                    <span>Applied {times_applied}×</span>
                </div>
                <div class="rule-card-meta-item">
                    <span>Utility {utility_sign}{utility:.2f}</span>
                </div>
                <div class="rule-card-meta-item">
                    <span>Source: {source}</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def activity_item(
    title: str,
    time: str,
    status: Literal["success", "failed", "info"] = "info",
    badge: str | None = None,
):
    """Render a timeline-style activity item.

    Args:
        title: Activity description (e.g., "Deploy Node.js app")
        time: Relative time (e.g., "2h ago", "yesterday")
        status: Outcome type (success=green, failed=red, info=blue)
        badge: Optional badge text (e.g., "success", "failed")
    """
    badge_html = ""
    if badge:
        badge_html = f'<span class="activity-badge {status}">{badge}</span>'

    st.markdown(
        f"""
        <div class="activity-item {status}">
            <div class="activity-content">
                <div class="activity-title">{title}</div>
                <div class="activity-time">{time}</div>
            </div>
            {badge_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str | None = None):
    """Render a page header with title and optional subtitle.

    Args:
        title: Page title (left-aligned, tight tracking)
        subtitle: Optional description text
    """
    subtitle_html = f'<div class="page-subtitle">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f"""
        <div class="page-title">{title}</div>
        {subtitle_html}
        """,
        unsafe_allow_html=True,
    )


def section_header(title: str):
    """Render a section header.

    Args:
        title: Section title
    """
    st.markdown(
        f'<div class="section-title">{title}</div>',
        unsafe_allow_html=True,
    )
