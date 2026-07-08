"""
Architecture Diagram — SVG/HTML visualization of Sage's 4-tier memory.

For the demo video and Devpost submission.
"""

ARCHITECTURE_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Sage Architecture — 4-Tier Cognitive Memory</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0a0a0a;
      color: #e0e0e0;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      margin: 0;
      padding: 20px;
    }
    .container {
      max-width: 900px;
      width: 100%;
    }
    h1 {
      text-align: center;
      font-size: 2.5em;
      margin-bottom: 10px;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .subtitle {
      text-align: center;
      color: #888;
      margin-bottom: 40px;
      font-size: 1.1em;
    }
    .flow {
      display: flex;
      flex-direction: column;
      gap: 0;
      align-items: center;
    }
    .tier {
      width: 100%;
      max-width: 700px;
      padding: 20px 30px;
      border-radius: 12px;
      position: relative;
      margin: 8px 0;
    }
    .tier-working {
      background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
      border: 2px solid #667eea;
    }
    .tier-procedural {
      background: linear-gradient(135deg, #1a2e1a 0%, #162e1e 100%);
      border: 2px solid #00d4aa;
    }
    .tier-semantic {
      background: linear-gradient(135deg, #2e1a1a 0%, #2e1621 100%);
      border: 2px solid #ff6b6b;
    }
    .tier-episodic {
      background: linear-gradient(135deg, #2e2e1a 0%, #2e2116 100%);
      border: 2px solid #ffd93d;
    }
    .tier-title {
      font-size: 1.3em;
      font-weight: 700;
      margin-bottom: 8px;
    }
    .tier-working .tier-title { color: #667eea; }
    .tier-procedural .tier-title { color: #00d4aa; }
    .tier-semantic .tier-title { color: #ff6b6b; }
    .tier-episodic .tier-title { color: #ffd93d; }
    .tier-desc {
      color: #aaa;
      font-size: 0.95em;
    }
    .tier-example {
      background: rgba(255,255,255,0.05);
      padding: 8px 12px;
      border-radius: 6px;
      margin-top: 8px;
      font-family: 'Fira Code', monospace;
      font-size: 0.85em;
      color: #ccc;
    }
    .arrow {
      font-size: 1.8em;
      color: #555;
      text-align: center;
      margin: 4px 0;
    }
    .reflection-box {
      width: 100%;
      max-width: 700px;
      padding: 20px 30px;
      background: linear-gradient(135deg, #2e1a2e 0%, #2e1621 100%);
      border: 2px dashed #c084fc;
      border-radius: 12px;
      margin: 8px 0;
      text-align: center;
    }
    .reflection-box .tier-title { color: #c084fc; }
    .reflection-flow {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 20px;
      margin-top: 12px;
      font-size: 0.9em;
    }
    .reflection-step {
      background: rgba(192, 132, 252, 0.1);
      padding: 8px 14px;
      border-radius: 8px;
      border: 1px solid rgba(192, 132, 252, 0.3);
    }
    .label {
      text-align: center;
      font-size: 0.85em;
      color: #666;
      margin: 4px 0 0 0;
      font-style: italic;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>Sage</h1>
    <p class="subtitle">Self-Improving Agent with Cognitive Memory</p>

    <div class="flow">
      <div class="tier tier-working">
        <div class="tier-title">⚡ Working Memory</div>
        <div class="tier-desc">Current task context — ephemeral, single session</div>
        <div class="tier-example">"Deploy Node.js app to Alibaba Cloud ECS"</div>
      </div>

      <div class="arrow">↓</div>

      <div class="tier tier-procedural">
        <div class="tier-title">🧠 Procedural Memory</div>
        <div class="tier-desc">Self-learned rules from reflection — loaded into prompt at task start</div>
        <div class="tier-example">R001: Always configure security group rules before deploying applications to ECS.<br>
        R002: Verify language runtime is installed before deploying.<br>
        R003: Map container ports to host ports with -p flag.</div>
      </div>

      <div class="arrow">↓</div>

      <div class="tier tier-semantic">
        <div class="tier-title">📖 Semantic Memory</div>
        <div class="tier-desc">Knowledge base — reference material, docs, API specs</div>
        <div class="tier-example">alibaba-cloud-deployment.md — ECS setup guide<br>
        security-groups.md — firewall configuration rules</div>
      </div>

      <div class="arrow">↓</div>

      <div class="tier tier-episodic">
        <div class="tier-title">📚 Episodic Memory</div>
        <div class="tier-desc">Past experiences — JSONL log of all interactions and outcomes</div>
        <div class="tier-example">{ task: "Deploy web app", outcome: "failed", correction: "Configure security group", rule: "R001" }</div>
      </div>

      <div class="arrow">↓</div>

      <div class="reflection-box">
        <div class="tier-title">🔄 Reflection Engine</div>
        <div class="tier-desc">The core innovation — extracts general rules from specific corrections</div>
        <div class="reflection-flow">
          <div class="reflection-step">Detect Correction</div>
          <div>→</div>
          <div class="reflection-step">Analyze Error</div>
          <div>→</div>
          <div class="reflection-step">Generalize Rule</div>
          <div>→</div>
          <div class="reflection-step">Store in rules.md</div>
        </div>
      </div>

      <p class="label">Correction received → Reflection triggers → Rule stored → Next task applies rule</p>
    </div>
  </div>
</body>
</html>"""


def generate_architecture_diagram(output_path: str = "demo/architecture.html"):
    """Generate the architecture diagram HTML."""
    from pathlib import Path

    from sage.persistence import atomic_write_text

    path = Path(output_path)
    atomic_write_text(path, ARCHITECTURE_HTML)
    print(f"Architecture diagram saved to: {path}")
    return path


if __name__ == "__main__":
    generate_architecture_diagram()
