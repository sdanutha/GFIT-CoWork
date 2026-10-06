"""Regression coverage for cross-profile cron unread badges (#5960)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
ROUTES_PY = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")
NODE = shutil.which("node")

# Sibling harness scaffolding lives beside this file and is not on sys.path by
# default; import it the same way the other node-harness tests do.
sys.path.insert(0, str(ROOT / "tests"))
import _unread_store_helpers as unread_store_helpers  # noqa: E402


def _extract_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    if source[max(0, start - 6) : start] == "async ":
        start -= 6
    brace = source.index("{", start)
    depth = 1
    pos = brace + 1
    while depth and pos < len(source):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
        pos += 1
    assert depth == 0
    return source[start:pos]


def test_recent_handler_reuses_dispatcher_cron_context_without_nesting():
    from tests._route_source import route_source

    dispatch = route_source("GET", "/api/crons/recent")
    handler_start = ROUTES_PY.index("def _handle_cron_recent(")
    handler_end = ROUTES_PY.index("\ndef ", handler_start + 1)
    handler = ROUTES_PY[handler_start:handler_end]

    assert "with cron_profile_context():" in dispatch
    assert "cron_profile_context_for_home" not in handler


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_cron_poll_tags_persisted_markers_with_active_profile():
    polling = _extract_function(PANELS_JS, "startCronPolling")
    script = f"""
let _cronPollSince=10;
let _cronPollTimer=null;
let _cronUnreadCount=0;
let _cronPollGeneration=0;
const _cronNewJobIds=new Set();
const markCalls=[];
let intervalCallback=null;
global.document={{hidden:false}};
global.S={{activeProfile:'profile-a'}};
global.setInterval=(callback)=>{{ intervalCallback=callback; return 1; }};
global.api=async()=>({{
  completions:[{{
    job_id:'job-a',
    session_id:'cron-session-a',
    message_count:3,
    completed_at:20,
    toast_notifications:false,
  }}]
}});
global.showToast=()=>{{}};
global.t=(key)=>key;
global.updateCronBadge=()=>{{ _cronUnreadCount=_cronNewJobIds.size; }};
function _markSessionCompletionUnreadIfBackground(sid, count, meta){{
  markCalls.push([sid, count, meta]);
}}
{polling}
startCronPolling();
(async()=>{{
  await intervalCallback();
  process.stdout.write(JSON.stringify({{markCalls, unreadJobs:Array.from(_cronNewJobIds)}}));
}})().catch(error=>{{ console.error(error); process.exit(1); }});
"""
    result = subprocess.run(
        [NODE, "-e", script], check=True, capture_output=True, text=True, timeout=30
    )
    state = json.loads(result.stdout)
    assert state["unreadJobs"] == ["job-a"]
    assert state["markCalls"] == [
        ["cron-session-a", 3, {"source": "cron", "profile": "profile-a"}]
    ]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_session_list_path_tags_cron_markers_with_source_and_profile():
    """Re-gate #5975: _markPollingCompletionUnreadTransitions must tag cron rows."""
    mark_poll = _extract_function(SESSIONS_JS, "_markPollingCompletionUnreadTransitions")
    is_cron = _extract_function(SESSIONS_JS, "_isCronSessionForUnread")
    meta_fn = _extract_function(SESSIONS_JS, "_cronCompletionUnreadMetaForSession")
    source_key = _extract_function(SESSIONS_JS, "_sourceKeyForSession")
    match_fn = _extract_function(SESSIONS_JS, "_cronMarkerProfileMatchesActive")
    profile_match = _extract_function(SESSIONS_JS, "_profileMatchesActiveProfile")
    script = f"""
const markCalls=[];
global.S={{activeProfile:'profile-a',activeProfileIsDefault:false}};
let _showAllProfiles=false;
global._allSessions=[];
global._sessionListSnapshotById=new Map();
global._sessionStreamingById=new Map();
global._sessionListSourceById=new Map();
global._allSessionsScope=null;
function _getSessionObservedStreaming(){{ return {{}}; }}
function _isSessionEffectivelyStreaming(){{ return false; }}
function _hasPendingUserMessageSignal(){{ return false; }}
function _isSessionActivelyViewedForList(){{ return false; }}
function _rememberSessionListSource(){{}}
function _rememberObservedStreamingSession(){{}}
function _forgetObservedStreamingSession(){{}}
function _setSessionViewedCount(){{}}
function _markSessionCompletionUnread(sid, count, meta){{
  markCalls.push([sid, count, meta||null]);
}}
{source_key}
{is_cron}
{meta_fn}
{profile_match}
{match_fn}
{mark_poll}
const sessions=[
  {{
    session_id:'cron-from-list',
    message_count:2,
    last_message_at:20,
    source_tag:'cron',
    profile:'profile-a',
    is_streaming:false,
  }},
  {{
    session_id:'chat-from-list',
    message_count:5,
    last_message_at:30,
    source_tag:'webui',
    profile:'profile-a',
    is_streaming:false,
  }},
];
// Pretend both previously streaming so completion transition fires.
_sessionStreamingById.set('cron-from-list', true);
_sessionStreamingById.set('chat-from-list', true);
_sessionListSnapshotById.set('cron-from-list', {{message_count:1, last_message_at:10}});
_sessionListSnapshotById.set('chat-from-list', {{message_count:4, last_message_at:10}});
_markPollingCompletionUnreadTransitions(sessions);
process.stdout.write(JSON.stringify({{markCalls}}));
"""
    result = subprocess.run(
        [NODE, "-e", script], check=True, capture_output=True, text=True, timeout=30
    )
    state = json.loads(result.stdout)
    by_sid = {row[0]: row for row in state["markCalls"]}
    assert "cron-from-list" in by_sid
    assert by_sid["cron-from-list"][2] == {"source": "cron", "profile": "profile-a"}
    assert "chat-from-list" in by_sid
    assert by_sid["chat-from-list"][2] is None


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_fresh_session_list_still_marks_when_unread_gen_matches():
    """Sanity: matching unreadGen still allows completion marks after switch."""
    sessions_js = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
    apply_fn = _extract_function(sessions_js, "_applySessionListPayload")
    mark_poll = _extract_function(sessions_js, "_markPollingCompletionUnreadTransitions")
    helpers = "\n".join(
        [
            # Merge/tombstone helpers the unread persistence paths call.
            unread_store_helpers.BLOCK,
            _extract_function(sessions_js, "_isCronSessionForUnread"),
            _extract_function(sessions_js, "_sourceKeyForSession"),
            _extract_function(sessions_js, "_cronCompletionUnreadMetaForSession"),
            _extract_function(sessions_js, "_cronMarkerProfileMatchesActive"),
            _extract_function(sessions_js, "_profileMatchesActiveProfile"),
            _extract_function(sessions_js, "_getSessionCompletionUnread"),
            _extract_function(sessions_js, "_saveSessionCompletionUnread"),
            _extract_function(sessions_js, "_markSessionCompletionUnread"),
            _extract_function(sessions_js, "_hasSessionCompletionUnread"),
            _extract_function(sessions_js, "_hasUnreadForSession"),
        ]
    )
    script = f"""
const store={{'hermes-session-completion-unread':JSON.stringify({{}})}};
global.localStorage={{
  getItem:(key)=>Object.prototype.hasOwnProperty.call(store,key)?store[key]:null,
  setItem:(key,value)=>{{ store[key]=String(value); }},
  removeItem:(key)=>{{ delete store[key]; }},
  key:(i)=>Object.keys(store)[i]??null,
  get length(){{ return Object.keys(store).length; }},
}};
let _sessionCompletionUnread=null;
let _sessionViewedCounts={{}};
const SESSION_COMPLETION_UNREAD_KEY='hermes-session-completion-unread';
let _cronPollGeneration=3;
let _allSessions=[];
let _allSessionsScope=null;
let _sidebarReferenceSessions=[];
let _otherProfileCount=0;
let _archivedWebuiCount=0;
let _archivedCliCount=0;
let _serverWebuiSessionCount=null;
let _serverCliSessionCount=null;
let _serverTimeDelta=0;
let _serverTz=null;
let _sessionListLoadError=null;
let _sessionListHasLoadedOnce=false;
let _sessionListFirstRenderAnimated=true;
let _showAllProfiles=false;
const _optimisticallyRemovedSessionIds=new Set();
const _sessionStreamingById=new Map([['new-cron', true]]);
const _sessionListSnapshotById=new Map([['new-cron', {{message_count:1, last_message_at:10}}]]);
const _sessionListSourceById=new Map();
global.S={{activeProfile:'profile-b',activeProfileIsDefault:false}};
global.renderSessionListFromCache=()=>{{}};
function _getSessionViewedCounts(){{ return _sessionViewedCounts; }}
function _setSessionViewedCount(){{}}
function _getSessionObservedStreaming(){{ return {{}}; }}
function _isSessionEffectivelyStreaming(){{ return false; }}
function _hasPendingUserMessageSignal(){{ return false; }}
function _isSessionActivelyViewedForList(){{ return false; }}
function _rememberSessionListSource(){{}}
function _rememberObservedStreamingSession(){{}}
function _forgetObservedStreamingSession(){{}}
function _reconcileActiveSessionIdleStateFromList(){{}}
function _mergeOptimisticFirstTurnSessions(s){{ return s; }}
function _recordSessionProfileCount(){{}}
function _syncSessionAttentionSoundState(){{}}
function _pruneLineageReportCacheToVisibleSessions(){{}}
function _requestedSessionSidebarSource(){{ return 'webui'; }}
function _sessionListExcludeHiddenEnabled(){{ return false; }}
function startStreamingPoll(){{}}
function stopStreamingPoll(){{}}
function ensureSessionTimeRefreshPoll(){{}}
function ensureActiveSessionExternalRefreshPoll(){{}}
function animateNextSessionListRefresh(){{}}
function ensureSessionEventsSSE(){{}}
function _sessionListRenderSignature(){{ return 'sig'; }}
function _purgeStaleInflightEntries(){{}}
let _sessionListSkeletonActive=false;
let _sessionListRefreshAnimationPending=false;
let _lastSessionListRenderSig=null;
let _renamingSid=null;
let _sessionActionMenu=null;
let _allProjects=[];
{helpers}
{mark_poll}
{apply_fn}
_applySessionListPayload({{
  sessions:[{{
    session_id:'new-cron',
    message_count:2,
    last_message_at:20,
    source_tag:'cron',
    profile:'profile-b',
    is_streaming:false,
  }}],
  active_profile:'profile-b',
}}, {{projects:[]}}, {{unreadGen:3}});
const persisted=JSON.parse(store['hermes-session-completion-unread']||'{{}}');
process.stdout.write(JSON.stringify({{
  marked:Object.prototype.hasOwnProperty.call(persisted,'new-cron'),
  meta:persisted['new-cron']||null,
}}));
"""
    result = subprocess.run(
        [NODE, "-e", script], check=True, capture_output=True, text=True, timeout=30
    )
    state = json.loads(result.stdout)
    assert state["marked"] is True
    assert state["meta"]["source"] == "cron"
    assert state["meta"]["profile"] == "profile-b"
