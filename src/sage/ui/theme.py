"""Design system tokens and CSS injection for Sage UI."""

import streamlit as st

THEME_CSS = """
<style>
.metric-card {
  padding: 16px;
  border: 1px solid var(--secondary-background-color);
  border-radius: 8px;
  background: var(--background-color);
  margin-bottom: 8px;
  transition: all 0.2s ease;
}

.metric-card:hover {
  border-color: var(--primary-color);
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

.metric-value {
  font-size: 32px;
  font-weight: 600;
  color: var(--text-color);
  line-height: 1;
  margin-bottom: 4px;
  font-variant-numeric: tabular-nums;
}

.metric-label {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--secondary-text-color);
  margin-bottom: 0;
}

.metric-delta {
  font-size: 13px;
  font-weight: 500;
  margin-top: 4px;
}

.metric-delta.positive {
  color: var(--success-color, #09ab3b);
}

.metric-delta.negative {
  color: var(--error-color, #ff4b4b);
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 16px;
  font-size: 13px;
  font-weight: 500;
  background: var(--secondary-background-color);
  border: 1px solid var(--secondary-background-color);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.status-dot.connected {
  background: var(--success-color, #09ab3b);
}

.status-dot.simulated {
  background: var(--warning-color, #ffa421);
}

.status-dot.disconnected {
  background: var(--error-color, #ff4b4b);
}

.empty-state {
  text-align: center;
  padding: 48px 24px;
  border: 1px solid var(--secondary-background-color);
  border-radius: 8px;
  background: var(--background-color);
  margin: 24px 0;
}

.empty-state-icon {
  font-size: 48px;
  margin-bottom: 16px;
  opacity: 0.4;
}

.empty-state-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-color);
  margin-bottom: 8px;
}

.empty-state-description {
  font-size: 14px;
  color: var(--secondary-text-color);
  line-height: 1.6;
  margin-bottom: 16px;
  max-width: 400px;
  margin-left: auto;
  margin-right: auto;
}

.rule-card {
  padding: 16px;
  border: 1px solid var(--secondary-background-color);
  border-radius: 8px;
  background: var(--background-color);
  margin-bottom: 12px;
  transition: all 0.2s ease;
}

.rule-card:hover {
  border-color: var(--primary-color);
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

.rule-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.rule-card-id {
  font-size: 13px;
  font-weight: 600;
  color: var(--primary-color);
  font-family: monospace;
}

.rule-card-confidence {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color);
  font-variant-numeric: tabular-nums;
}

.rule-card-text {
  font-size: 14px;
  color: var(--text-color);
  line-height: 1.5;
  margin-bottom: 12px;
}

.rule-card-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--secondary-text-color);
  margin-bottom: 12px;
}

.rule-card-meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.activity-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 0;
  border-left: 2px solid var(--secondary-background-color);
  padding-left: 16px;
  margin-left: 8px;
  position: relative;
}

.activity-item::before {
  content: '';
  position: absolute;
  left: -6px;
  top: 16px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--secondary-background-color);
  border: 2px solid var(--background-color);
}

.activity-item.success::before {
  background: var(--success-color, #09ab3b);
}

.activity-item.failed::before {
  background: var(--error-color, #ff4b4b);
}

.activity-item.info::before {
  background: var(--primary-color);
}

.activity-content {
  flex: 1;
}

.activity-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-color);
  margin-bottom: 2px;
}

.activity-time {
  font-size: 12px;
  color: var(--secondary-text-color);
}

.activity-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.activity-badge.success {
  background: rgba(9, 171, 59, 0.1);
  color: var(--success-color, #09ab3b);
}

.activity-badge.failed {
  background: rgba(255, 75, 75, 0.1);
  color: var(--error-color, #ff4b4b);
}

.page-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-color);
  margin-bottom: 8px;
  letter-spacing: -0.02em;
}

.page-subtitle {
  font-size: 14px;
  color: var(--secondary-text-color);
  margin-bottom: 32px;
}

.section-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-color);
  margin-top: 32px;
  margin-bottom: 16px;
  letter-spacing: -0.01em;
}

.sidebar-section-title {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--secondary-text-color);
  margin-bottom: 8px;
  padding-left: 8px;
}

@media (max-width: 768px) {
  .metric-value {
    font-size: 24px;
  }
  
  .page-title {
    font-size: 24px;
  }
  
  .section-title {
    font-size: 18px;
  }
}
</style>
"""


def inject_theme_css():
    """Inject the design system CSS. Call once at app startup."""
    st.markdown(THEME_CSS, unsafe_allow_html=True)
