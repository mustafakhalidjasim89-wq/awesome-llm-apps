import asyncio
import io
import json
import os
import re
import tempfile
import zipfile
import streamlit as st

# Import the existing SkillOptimizer and default model from your backend module
from adk_optimizer import DEFAULT_MODEL, SkillOptimizer

# -----------------------------------------------------------------------------
# Configuration & Page Setup
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Multi-Agent Skill Optimizer",
    page_icon="🤖",
    layout="wide",
)

MODEL_SUGGESTIONS = [
    DEFAULT_MODEL,
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
]


def parse_skill_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from SKILL.md content."""
    if not content.startswith("---"):
        return {}
    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        frontmatter = parts[1].strip()
        metadata = {}
        for line in frontmatter.split("\n"):
            line = line.strip()
            if ":" in line and not line.startswith(" "):
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip('"')
        return metadata
    except Exception:
        return {}


# Initialize Session State Variables
if "skill_files" not in st.session_state:
    st.session_state.skill_files = {}
if "file_list" not in st.session_state:
    st.session_state.file_list = []
if "metadata" not in st.session_state:
    st.session_state.metadata = {}
if "scenarios" not in st.session_state:
    st.session_state.scenarios = []
if "evals" not in st.session_state:
    st.session_state.evals = []
if "optimization_results" not in st.session_state:
    st.session_state.optimization_results = None

# -----------------------------------------------------------------------------
# Sidebar: Credentials & Model Configuration
# -----------------------------------------------------------------------------
st.sidebar.title("⚙️ Configuration")

api_key = st.sidebar.text_input(
    "Google Gemini API Key",
    type="password",
    help="Required for running Google ADK agents.",
)

selected_model = st.sidebar.selectbox(
    "Select Model",
    options=list(dict.fromkeys(MODEL_SUGGESTIONS)),
    index=0,
)

max_rounds = st.sidebar.slider(
    "Max Optimization Rounds",
    min_value=1,
    max_value=20,
    value=5,
)

st.title("🛠️ Multi-Agent Skill Optimizer")
st.caption("Powered by Google ADK & Gemini")

# -----------------------------------------------------------------------------
# Step 1: Upload Skill Files
# -----------------------------------------------------------------------------
st.header("1. Upload Skill Bundle")

uploaded_file = st.file_uploader(
    "Upload skill as a ZIP archive containing SKILL.md and assets",
    type=["zip"],
)

if uploaded_file is not None:
    try:
        with zipfile.ZipFile(io.BytesIO(uploaded_file.read())) as zf:
            skill_files = {}
            file_list = []
            for name in zf.namelist():
                if (
                    name.endswith("/")
                    or name.startswith("__MACOSX")
                    or ".DS_Store" in name
                ):
                    continue
                raw_bytes = zf.read(name)
                text_content = raw_bytes.decode("utf-8", errors="ignore")
                skill_files[name] = text_content
                file_list.append(name)

            # Normalize directory path if zipped inside a root folder
            if file_list:
                common_path = os.path.commonpath(file_list)
                if common_path and common_path != file_list[0]:
                    skill_files = {
                        os.path.relpath(k, common_path): v
                        for k, v in skill_files.items()
                    }
                    file_list = [os.path.relpath(f, common_path) for f in file_list]

            skill_md_key = next((k for k in skill_files if k.endswith("SKILL.md")), None)
            if not skill_md_key:
                st.error("Uploaded ZIP does not contain a valid `SKILL.md` file.")
            else:
                st.session_state.skill_files = skill_files
                st.session_state.file_list = file_list
                st.session_state.metadata = parse_skill_frontmatter(
                    skill_files[skill_md_key]
                )
                st.success(f"Successfully loaded {len(file_list)} file(s).")
    except Exception as e:
        st.error(f"Error extracting ZIP archive: {str(e)}")

if st.session_state.metadata:
    with st.expander("📄 Parsed Skill Metadata", expanded=False):
        st.json(st.session_state.metadata)
        st.text("Files contained in bundle:")
        st.write(st.session_state.file_list)

# -----------------------------------------------------------------------------
# Step 2: Analyze Skill & Edit Benchmarks
# -----------------------------------------------------------------------------
st.header("2. Analyze & Generate Evaluation Benchmarks")

if st.button("🔍 Analyze Skill", disabled=not st.session_state.skill_files):
    if not api_key:
        st.error("Please enter a valid Gemini API Key in the sidebar.")
    else:
        with st.spinner("Analyzing skill and generating test scenarios/evals..."):
            optimizer = SkillOptimizer(api_key=api_key, model=selected_model)
            analysis_result = asyncio.run(
                optimizer.analyze_skill(st.session_state.skill_files)
            )
            st.session_state.scenarios = analysis_result.get("scenarios", [])
            st.session_state.evals = analysis_result.get("evals", [])
            st.success("Analysis complete!")

if st.session_state.scenarios or st.session_state.evals:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Test Scenarios")
        scenarios_text = st.text_area(
            "Edit Test Scenarios (JSON format)",
            value=json.dumps(st.session_state.scenarios, indent=2),
            height=300,
        )
        try:
            st.session_state.scenarios = json.loads(scenarios_text)
        except Exception:
            st.warning("Invalid JSON in Test Scenarios.")

    with col2:
        st.subheader("Evaluation Criteria")
        evals_text = st.text_area(
            "Edit Evaluation Criteria (JSON format)",
            value=json.dumps(st.session_state.evals, indent=2),
            height=300,
        )
        try:
            st.session_state.evals = json.loads(evals_text)
        except Exception:
            st.warning("Invalid JSON in Evaluation Criteria.")

# -----------------------------------------------------------------------------
# Step 3: Run Optimization Workflow
# -----------------------------------------------------------------------------
st.header("3. Optimize Skill")

ready_to_optimize = (
    bool(st.session_state.skill_files)
    and bool(st.session_state.scenarios)
    and bool(st.session_state.evals)
)

if st.button("🚀 Start Optimization Loop", disabled=not ready_to_optimize):
    if not api_key:
        st.error("Please enter a valid Gemini API Key in the sidebar.")
    else:
        status_box = st.empty()
        progress_bar = st.progress(0)
        logs_container = st.container()

        optimizer = SkillOptimizer(api_key=api_key, model=selected_model)

        async def streamlit_callback(event):
            event_type = event.get("type")
            data = event.get("data", {})

            if event_type == "baseline":
                status_box.info(
                    f"Baseline Evaluation Score: **{data.get('score')}%**"
                )
            elif event_type == "experiment_start":
                rnd = data.get("round")
                progress_bar.progress(int((rnd / max_rounds) * 100))
                logs_container.write(f"🔄 **Round {rnd}/{max_rounds} started...**")
            elif event_type == "experiment_result":
                status = "✅ KEPT" if data.get("kept") else "❌ DISCARDED"
                logs_container.write(
                    f"Round {data.get('round')}: Score = **{data.get('score')}%** [{status}] | "
                    f"Strategy: *{data.get('strategy')}* — {data.get('description')}"
                )
            elif event_type == "complete":
                progress_bar.progress(100)
                status_box.success("Optimization finished!")

        with st.spinner("Agents optimizing skill..."):
            result = asyncio.run(
                optimizer.optimize(
                    skill_files=st.session_state.skill_files,
                    scenarios=st.session_state.scenarios,
                    evals=st.session_state.evals,
                    max_rounds=max_rounds,
                    callback=streamlit_callback,
                )
            )
            st.session_state.optimization_results = result

# -----------------------------------------------------------------------------
# Step 4: Display Results & Download Bundle
# -----------------------------------------------------------------------------
if st.session_state.optimization_results:
    st.header("4. Results & Download")
    res = st.session_state.optimization_results

    metric_col1, metric_col2 = st.columns(2)
    metric_col1.metric("Baseline Score", f"{res.get('baseline_score')}%")
    metric_col2.metric("Final Score", f"{res.get('final_score')}%")

    st.subheader("Score History")
    st.line_chart(res.get("score_history", []))

    st.subheader("Mutation Log")
    st.dataframe(res.get("mutation_log", []))

    st.subheader("Optimized SKILL.md Content")
    st.code(res.get("improved_skill_md", ""), language="markdown")

    # Packaging optimized skill into downloadable zip
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        for filename, content in st.session_state.skill_files.items():
            if filename.endswith("SKILL.md"):
                zf.writestr(filename, res.get("improved_skill_md", content))
            else:
                zf.writestr(filename, content)
        zf.writestr(
            "CHANGELOG.json",
            json.dumps(res.get("mutation_log", []), indent=2),
        )

    st.download_button(
        label="📥 Download Improved Skill ZIP",
        data=zip_buffer.getvalue(),
        file_name="improved_skill.zip",
        mime="application/zip",
    )
