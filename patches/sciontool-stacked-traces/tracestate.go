/*
Copyright 2026 The Scion Authors.
*/

package telemetry

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"syscall"
	"time"

	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
)

type explicitIDsKey struct{}

type explicitIDs struct {
	traceID trace.TraceID
	spanID  trace.SpanID
}

// ContextWithExplicitIDs attaches an explicit TraceID and/or SpanID to ctx so
// the TracerProvider's IDGenerator uses them when starting a span.
func ContextWithExplicitIDs(ctx context.Context, tid trace.TraceID, sid trace.SpanID) context.Context {
	return context.WithValue(ctx, explicitIDsKey{}, explicitIDs{traceID: tid, spanID: sid})
}

type explicitIDGenerator struct{}

// NewExplicitIDGenerator returns an IDGenerator that honors ContextWithExplicitIDs.
func NewExplicitIDGenerator() sdktrace.IDGenerator {
	return explicitIDGenerator{}
}

func (explicitIDGenerator) NewIDs(ctx context.Context) (trace.TraceID, trace.SpanID) {
	if v, ok := ctx.Value(explicitIDsKey{}).(explicitIDs); ok {
		tid := v.traceID
		sid := v.spanID
		if !tid.IsValid() {
			_, _ = rand.Read(tid[:])
		}
		if !sid.IsValid() {
			_, _ = rand.Read(sid[:])
		}
		return tid, sid
	}
	var tid trace.TraceID
	var sid trace.SpanID
	_, _ = rand.Read(tid[:])
	_, _ = rand.Read(sid[:])
	return tid, sid
}

func (explicitIDGenerator) NewSpanID(ctx context.Context, _ trace.TraceID) trace.SpanID {
	if v, ok := ctx.Value(explicitIDsKey{}).(explicitIDs); ok && v.spanID.IsValid() {
		return v.spanID
	}
	var sid trace.SpanID
	_, _ = rand.Read(sid[:])
	return sid
}

// PendingSpan represents an in-flight model or tool invocation across hook processes.
type PendingSpan struct {
	SpanID    string `json:"span_id"`
	Name      string `json:"name"`
	StartNano int64  `json:"start_nano"`
}

// TraceState persists active conversation/turn trace context across short-lived
// sciontool hook and sciontool status subprocesses and the PID 1 receiver.
type TraceState struct {
	SessionID        string        `json:"session_id"`
	TurnIndex        int64         `json:"turn_index"`
	TraceID          string        `json:"trace_id"`
	PrevTraceID      string        `json:"prev_trace_id,omitempty"`
	PrevTurnSpanID   string        `json:"prev_turn_span_id,omitempty"`
	TurnSpanID       string        `json:"turn_span_id"`
	TurnStartNano    int64         `json:"turn_start_nano"`
	LastActivityNano int64         `json:"last_activity_nano"`
	TurnFinalized    bool          `json:"turn_finalized"`
	ModelCalls       int64         `json:"model_calls"`
	ToolCalls        int64         `json:"tool_calls"`
	InputTokens      int64         `json:"input_tokens"`
	OutputTokens     int64         `json:"output_tokens"`
	CachedTokens     int64         `json:"cached_tokens"`
	LastStatus       string        `json:"last_status,omitempty"`
	LastStatusMsg    string        `json:"last_status_msg,omitempty"`
	PendingModel     *PendingSpan  `json:"pending_model,omitempty"`
	PendingTools     []PendingSpan `json:"pending_tools,omitempty"`
}

// TraceStateFile returns the path to the cross-process trace state file.
func TraceStateFile() string {
	if p := os.Getenv("SCION_TRACE_STATE_FILE"); p != "" {
		return p
	}
	if fi, err := os.Stat("/home/scion/.scion"); err == nil && fi.IsDir() {
		return "/home/scion/.scion/trace-state.json"
	}
	return "/tmp/scion-trace-state.json"
}

// WithTraceState locks, loads, mutates, and saves the cross-process TraceState.
func WithTraceState(fn func(st *TraceState) error) error {
	statePath := TraceStateFile()
	lockPath := statePath + ".lock"
	lockFile, err := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0666)
	if err != nil {
		var st TraceState
		return fn(&st)
	}
	_ = os.Chmod(lockPath, 0666)
	defer func() {
		_ = syscall.Flock(int(lockFile.Fd()), syscall.LOCK_UN)
		_ = lockFile.Close()
	}()
	_ = syscall.Flock(int(lockFile.Fd()), syscall.LOCK_EX)

	var st TraceState
	rawBefore, err := os.ReadFile(statePath)
	if err == nil && len(rawBefore) > 0 {
		_ = json.Unmarshal(rawBefore, &st)
	}

	if err := fn(&st); err != nil {
		return err
	}

	if out, err := json.Marshal(&st); err == nil {
		if !bytes.Equal(bytes.TrimSpace(rawBefore), bytes.TrimSpace(out)) {
			if f, err := os.OpenFile(statePath, os.O_CREATE|os.O_RDWR|os.O_TRUNC, 0666); err == nil {
				_, _ = f.Write(out)
				_ = f.Close()
				_ = os.Chmod(statePath, 0666)
			}
		}
	}
	return nil
}

// NewRandomTraceIDHex generates a 32-char hex (16-byte) TraceID.
func NewRandomTraceIDHex() string {
	var tid trace.TraceID
	_, _ = rand.Read(tid[:])
	if !tid.IsValid() {
		tid[0] = 1
	}
	return hex.EncodeToString(tid[:])
}

// NewRandomSpanIDHex generates a 16-char hex (8-byte) SpanID.
func NewRandomSpanIDHex() string {
	var sid trace.SpanID
	_, _ = rand.Read(sid[:])
	if !sid.IsValid() {
		sid[0] = 1
	}
	return hex.EncodeToString(sid[:])
}

// ParseTraceIDHex parses a 32-char hex string into trace.TraceID.
func ParseTraceIDHex(h string) (trace.TraceID, bool) {
	var tid trace.TraceID
	b, err := hex.DecodeString(h)
	if err != nil || len(b) != 16 {
		return tid, false
	}
	copy(tid[:], b)
	return tid, tid.IsValid()
}

// ParseSpanIDHex parses a 16-char hex string into trace.SpanID.
func ParseSpanIDHex(h string) (trace.SpanID, bool) {
	var sid trace.SpanID
	b, err := hex.DecodeString(h)
	if err != nil || len(b) != 8 {
		return sid, false
	}
	copy(sid[:], b)
	return sid, sid.IsValid()
}

// EnsureActiveTurn ensures st has an active unfinalized turn at nowNano.
// If forceNew is true and an existing unfinalized turn had activity, a copy of
// that previous turn is returned so the caller can export its root span.
func (st *TraceState) EnsureActiveTurn(nowNano int64, sessionID string, forceNew bool) *TraceState {
	if nowNano <= 0 {
		nowNano = time.Now().UnixNano()
	}
	var prevUnfinalized *TraceState
	sessionChanged := sessionID != "" && st.SessionID != "" && st.SessionID != sessionID
	idleExpired := st.PendingModel == nil && len(st.PendingTools) == 0 && st.LastActivityNano > 0 && (nowNano-st.LastActivityNano) > int64(3*time.Minute)

	needNew := st.TraceID == "" || st.TurnSpanID == "" || st.TurnFinalized || forceNew || sessionChanged || idleExpired
	if needNew {
		if st.TraceID != "" && st.TurnSpanID != "" && !st.TurnFinalized && (st.ModelCalls > 0 || st.ToolCalls > 0 || st.LastStatus != "") {
			cp := *st
			if cp.LastActivityNano < cp.TurnStartNano {
				cp.LastActivityNano = nowNano
			}
			prevUnfinalized = &cp
		}
		if sessionChanged {
			st.TurnIndex = 0
			st.PrevTraceID = ""
			st.PrevTurnSpanID = ""
		} else if st.TraceID != "" && st.TurnSpanID != "" {
			st.PrevTraceID = st.TraceID
			st.PrevTurnSpanID = st.TurnSpanID
		}
		st.TurnIndex++
		st.TraceID = NewRandomTraceIDHex()
		st.TurnSpanID = NewRandomSpanIDHex()
		st.TurnStartNano = nowNano
		st.LastActivityNano = nowNano
		st.TurnFinalized = false
		st.ModelCalls = 0
		st.ToolCalls = 0
		st.InputTokens = 0
		st.OutputTokens = 0
		st.CachedTokens = 0
		st.LastStatus = ""
		st.LastStatusMsg = ""
		st.PendingModel = nil
		st.PendingTools = nil
	}
	if sessionID != "" {
		st.SessionID = sessionID
	}
	if st.TurnStartNano <= 0 || nowNano < st.TurnStartNano {
		st.TurnStartNano = nowNano
	}
	if nowNano > st.LastActivityNano {
		st.LastActivityNano = nowNano
	}
	return prevUnfinalized
}

// SanitizeSpanSuffix sanitizes a suffix so "prefix/" + suffix satisfies safeSignalName.
func SanitizeSpanSuffix(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	var b strings.Builder
	lastUnderscore := false
	for i := 0; i < len(s); i++ {
		c := s[i]
		valid := (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '.' || c == ':' || c == '-' || c == '/'
		if valid {
			b.WriteByte(c)
			lastUnderscore = (c == '_')
		} else if !lastUnderscore {
			b.WriteByte('_')
			lastUnderscore = true
		}
		if b.Len() >= 64 {
			break
		}
	}
	return strings.Trim(b.String(), "_/-.:")
}

// TurnRootSpanName returns the root span name for an agent turn.
func TurnRootSpanName() string {
	if slug := SanitizeSpanSuffix(os.Getenv("SCION_AGENT_SLUG")); slug != "" {
		return "agent.turn/" + slug
	}
	return "agent.turn.end"
}

// ModelSpanName returns the child span name for an LLM API response.
func ModelSpanName(model string) string {
	if model == "" {
		model = os.Getenv("SCION_MODEL")
	}
	if s := SanitizeSpanSuffix(model); s != "" {
		return "gen_ai.api.response/" + s
	}
	return "gen_ai.api.response"
}

// ToolSpanName returns the child span name for a tool execution result.
func ToolSpanName(toolName string) string {
	if s := SanitizeSpanSuffix(toolName); s != "" {
		return "agent.tool.result/" + s
	}
	return "agent.tool.result"
}

// StatusSpanName returns the child span name for a sciontool status update.
func StatusSpanName(statusType string) string {
	if s := SanitizeSpanSuffix(statusType); s != "" {
		return "agent.status/" + s
	}
	return "agent.status"
}

// BuildTurnRootProtoSpan constructs an OTLP ResourceSpans for the root span of a finalized turn.
func BuildTurnRootProtoSpan(st *TraceState, endNano int64) []*tracepb.ResourceSpans {
	if st == nil || st.TraceID == "" || st.TurnSpanID == "" {
		return nil
	}
	tid, ok1 := ParseTraceIDHex(st.TraceID)
	sid, ok2 := ParseSpanIDHex(st.TurnSpanID)
	if !ok1 || !ok2 {
		return nil
	}
	startNano := st.TurnStartNano
	if startNano <= 0 {
		startNano = endNano - int64(10*time.Millisecond)
	}
	if endNano <= startNano {
		endNano = startNano + int64(time.Millisecond)
	}
	durationMs := (endNano - startNano) / int64(time.Millisecond)

	attrs := []*commonpb.KeyValue{
		{Key: "event.name", Value: stringProtoValue("agent.turn.end")},
		{Key: "turn.index", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: st.TurnIndex}}},
		{Key: "turn.model_calls", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: st.ModelCalls}}},
		{Key: "turn.tool_calls", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: st.ToolCalls}}},
		{Key: "duration_ms", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: durationMs}}},
		{Key: "success", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_BoolValue{BoolValue: true}}},
	}
	if st.SessionID != "" {
		attrs = append(attrs, &commonpb.KeyValue{Key: "session_id", Value: stringProtoValue(st.SessionID)})
	}
	if st.InputTokens > 0 {
		attrs = append(attrs, &commonpb.KeyValue{Key: "gen_ai.usage.input_tokens", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: st.InputTokens}}})
	}
	if st.OutputTokens > 0 {
		attrs = append(attrs, &commonpb.KeyValue{Key: "gen_ai.usage.output_tokens", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: st.OutputTokens}}})
	}
	if st.CachedTokens > 0 {
		attrs = append(attrs, &commonpb.KeyValue{Key: "gen_ai.usage.cached_tokens", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: st.CachedTokens}}})
	}
	if st.LastStatus != "" {
		attrs = append(attrs, &commonpb.KeyValue{Key: "agent.status", Value: stringProtoValue(st.LastStatus)})
	}
	if st.LastStatusMsg != "" {
		attrs = append(attrs, &commonpb.KeyValue{Key: "message", Value: stringProtoValue(st.LastStatusMsg)})
	}
	if st.PrevTraceID != "" {
		attrs = append(attrs, &commonpb.KeyValue{Key: "scion.previous_trace_id", Value: stringProtoValue(st.PrevTraceID)})
	}

	var links []*tracepb.Span_Link
	if prevTID, okT := ParseTraceIDHex(st.PrevTraceID); okT {
		if prevSID, okS := ParseSpanIDHex(st.PrevTurnSpanID); okS {
			links = append(links, &tracepb.Span_Link{
				TraceId: prevTID[:],
				SpanId:  prevSID[:],
				Attributes: []*commonpb.KeyValue{
					{Key: "link.type", Value: stringProtoValue("previous_turn")},
				},
			})
		}
	}

	resAttrs := authoritativeIdentity()
	resAttrs = append(resAttrs, &commonpb.KeyValue{Key: "service.name", Value: stringProtoValue("sciontool")})
	if gcpProj := os.Getenv(EnvProjectID); gcpProj != "" {
		resAttrs = append(resAttrs, &commonpb.KeyValue{Key: "gcp.project_id", Value: stringProtoValue(gcpProj)})
	}

	span := &tracepb.Span{
		TraceId:           tid[:],
		SpanId:            sid[:],
		Name:              TurnRootSpanName(),
		Kind:              tracepb.Span_SPAN_KIND_SERVER,
		StartTimeUnixNano: uint64(startNano),
		EndTimeUnixNano:   uint64(endNano),
		Attributes:        attrs,
		Links:             links,
		Status:            &tracepb.Status{Code: tracepb.Status_STATUS_CODE_OK},
	}

	return []*tracepb.ResourceSpans{
		{
			Resource: &resourcepb.Resource{Attributes: resAttrs},
			ScopeSpans: []*tracepb.ScopeSpans{
				{
					Scope: &commonpb.InstrumentationScope{Name: hookMetricScope},
					Spans: []*tracepb.Span{span},
				},
			},
		},
	}
}

// CorrelateAndSynthesizeClaudeSpans correlates native Claude Code OTLP log records
// with the active turn in TraceState and synthesizes child spans for api_request / api_error.
func CorrelateAndSynthesizeClaudeSpans(resourceLogs []*logspb.ResourceLogs) []*tracepb.ResourceSpans {
	if os.Getenv("SCION_AGENT_SLUG") == "" && os.Getenv("SCION_AGENT_ID") == "" {
		return nil
	}
	var outSpans []*tracepb.ResourceSpans
	for _, rl := range resourceLogs {
		if rl == nil {
			continue
		}
		for _, sl := range rl.ScopeLogs {
			if sl == nil || sl.GetScope().GetName() != claudeUsageScope {
				continue
			}
			for _, record := range sl.LogRecords {
				if record == nil {
					continue
				}
				eventName, _ := normalizedLogEventName(record, claudeUsageScope)
				sessionID := logAttrString(record.Attributes, "session.id")
				recNano := int64(record.TimeUnixNano)
				if recNano <= 0 {
					recNano = int64(record.ObservedTimeUnixNano)
				}
				if recNano <= 0 {
					recNano = time.Now().UnixNano()
				}

				_ = WithTraceState(func(st *TraceState) error {
					forceNew := eventName == "user_prompt"
					if prev := st.EnsureActiveTurn(recNano, sessionID, forceNew); prev != nil {
						outSpans = append(outSpans, BuildTurnRootProtoSpan(prev, prev.LastActivityNano)...)
					}
					tid, okT := ParseTraceIDHex(st.TraceID)
					turnSID, okS := ParseSpanIDHex(st.TurnSpanID)
					if !okT || !okS {
						return nil
					}

					if eventName == "api_request" || eventName == "api_error" {
						durMs, _ := logAttrInt(record.Attributes, "duration_ms")
						if durMs <= 0 {
							durMs = 250
						}
						startNano := recNano - durMs*int64(time.Millisecond)
						if startNano < st.TurnStartNano {
							st.TurnStartNano = startNano
						}
						modelSpanIDHex := NewRandomSpanIDHex()
						modelSID, _ := ParseSpanIDHex(modelSpanIDHex)

						record.TraceId = append([]byte(nil), tid[:]...)
						record.SpanId = append([]byte(nil), modelSID[:]...)

						model := logAttrString(record.Attributes, "model")
						inTok, _ := logAttrInt(record.Attributes, "input_tokens")
						outTok, _ := logAttrInt(record.Attributes, "output_tokens")
						cacheRead, _ := logAttrInt(record.Attributes, "cache_read_tokens")
						cacheWrite, _ := logAttrInt(record.Attributes, "cache_creation_tokens")
						ttftMs, _ := logAttrInt(record.Attributes, "ttft_ms")

						st.ModelCalls++
						st.InputTokens += inTok
						st.OutputTokens += outTok
						st.CachedTokens += cacheRead + cacheWrite
						if recNano > st.LastActivityNano {
							st.LastActivityNano = recNano
						}

						attrs := []*commonpb.KeyValue{
							{Key: "event.name", Value: stringProtoValue("gen_ai.api.response")},
							{Key: "event.dialect", Value: stringProtoValue("claude")},
							{Key: "duration_ms", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: durMs}}},
							{Key: "turn.index", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: st.TurnIndex}}},
						}
						if model != "" {
							attrs = append(attrs, &commonpb.KeyValue{Key: "gen_ai.request.model", Value: stringProtoValue(model)})
						}
						if sessionID != "" {
							attrs = append(attrs, &commonpb.KeyValue{Key: "session_id", Value: stringProtoValue(sessionID)})
						}
						if inTok > 0 {
							attrs = append(attrs, &commonpb.KeyValue{Key: "gen_ai.usage.input_tokens", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: inTok}}})
						}
						if outTok > 0 {
							attrs = append(attrs, &commonpb.KeyValue{Key: "gen_ai.usage.output_tokens", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: outTok}}})
						}
						if cacheRead+cacheWrite > 0 {
							attrs = append(attrs, &commonpb.KeyValue{Key: "gen_ai.usage.cached_tokens", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: cacheRead + cacheWrite}}})
						}
						if ttftMs > 0 {
							attrs = append(attrs, &commonpb.KeyValue{Key: "gen_ai.response.ttft_ms", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: ttftMs}}})
						}

						statusCode := tracepb.Status_STATUS_CODE_OK
						if eventName == "api_error" {
							statusCode = tracepb.Status_STATUS_CODE_ERROR
							if errStr := logAttrString(record.Attributes, "error"); errStr != "" {
								attrs = append(attrs, &commonpb.KeyValue{Key: "error", Value: stringProtoValue(errStr)})
							}
						} else {
							attrs = append(attrs, &commonpb.KeyValue{Key: "success", Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_BoolValue{BoolValue: true}}})
						}

						resAttrs := authoritativeIdentity()
						resAttrs = append(resAttrs, &commonpb.KeyValue{Key: "service.name", Value: stringProtoValue("sciontool")})
						if gcpProj := os.Getenv(EnvProjectID); gcpProj != "" {
							resAttrs = append(resAttrs, &commonpb.KeyValue{Key: "gcp.project_id", Value: stringProtoValue(gcpProj)})
						}

						span := &tracepb.Span{
							TraceId:           append([]byte(nil), tid[:]...),
							SpanId:            append([]byte(nil), modelSID[:]...),
							ParentSpanId:      append([]byte(nil), turnSID[:]...),
							Name:              ModelSpanName(model),
							Kind:              tracepb.Span_SPAN_KIND_CLIENT,
							StartTimeUnixNano: uint64(startNano),
							EndTimeUnixNano:   uint64(recNano),
							Attributes:        attrs,
							Status:            &tracepb.Status{Code: statusCode},
						}
						outSpans = append(outSpans, &tracepb.ResourceSpans{
							Resource: &resourcepb.Resource{Attributes: resAttrs},
							ScopeSpans: []*tracepb.ScopeSpans{
								{
									Scope: &commonpb.InstrumentationScope{Name: hookMetricScope},
									Spans: []*tracepb.Span{span},
								},
							},
						})
					} else if len(record.TraceId) == 0 {
						record.TraceId = append([]byte(nil), tid[:]...)
						record.SpanId = append([]byte(nil), turnSID[:]...)
					}
					return nil
				})
			}
		}
	}
	return outSpans
}

// CheckIdleTurnFinalization checks if an active turn in TraceState should be
// auto-finalized due to inactivity (e.g. Antigravity turns that end after PostInvocation).
func CheckIdleTurnFinalization(now time.Time) []*tracepb.ResourceSpans {
	if os.Getenv("SCION_AGENT_SLUG") == "" && os.Getenv("SCION_AGENT_ID") == "" {
		return nil
	}
	statePath := TraceStateFile()
	if _, err := os.Stat(statePath); err != nil {
		return nil
	}
	nowNano := now.UnixNano()
	var rootSpans []*tracepb.ResourceSpans
	_ = WithTraceState(func(st *TraceState) error {
		if st.TraceID == "" || st.TurnSpanID == "" || st.TurnFinalized {
			return nil
		}
		if st.ModelCalls == 0 && st.ToolCalls == 0 && st.LastStatus == "" {
			return nil
		}
		idleDur := time.Duration(nowNano - st.LastActivityNano)
		hasPending := st.PendingModel != nil || len(st.PendingTools) > 0
		harness := os.Getenv("SCION_HARNESS")

		shouldFinalize := false
		if !hasPending {
			if harness == "antigravity" && idleDur >= 6*time.Second {
				shouldFinalize = true
			} else if st.LastStatus != "" && idleDur >= 8*time.Second {
				shouldFinalize = true
			} else if idleDur >= 45*time.Second {
				shouldFinalize = true
			}
		} else if idleDur >= 3*time.Minute {
			shouldFinalize = true
		}

		if shouldFinalize {
			st.TurnFinalized = true
			st.PendingModel = nil
			st.PendingTools = nil
			rootSpans = BuildTurnRootProtoSpan(st, st.LastActivityNano)
		}
		return nil
	})
	return rootSpans
}
