const DRAG_THRESHOLD_PX = 5;
const NON_COMPONENT_DRAG_KINDS = new Set(["pin", "wire", "wire_stub", "junction", "net_label", "net_label_endpoint", "power_symbol", "ground_symbol", "power_label", "ground_label"]);
let state = { circuit: null, layout: null, scene: null, sceneById: new Map(), components: [], svg: "", scale: 1, panX: 0, panY: 0, selected: null, drag: null, suppressClick: false, current: null, catalog: null, expected: null, localFile: null, localLayoutFile: null };
const canvas = document.querySelector("#canvas");
const statusEl = document.querySelector("#status");

async function loadAll({ fresh = false } = {}) {
  try {
    const [components, circuit] = await Promise.all([
      fetchJson(`/api/components?t=${Date.now()}`, { cache: "no-store" }),
      fetchJson(`/api/circuit?t=${Date.now()}${fresh ? "&fresh=true" : ""}`, { cache: "no-store" })
    ]);
    state.circuit = circuit.circuit;
    state.layout = circuit.layout;
    state.svg = circuit.svg;
    state.current = circuit.current || null;
    state.expected = circuit.expected || null;
    state.components = components.components || [];
    renderLibrary(components.components || []);
    applySchematic(circuit);
    statusEl.textContent = `${state.circuit?.name || "Circuit"} loaded${fresh ? " from generated layout" : ""}`;
  } catch (err) {
    statusEl.textContent = `Reload failed: ${err.message}`;
  }
}

function applySchematic(payload, options = {}) {
  const schematic = payload.schematic || payload;
  state.circuit = schematic.circuit;
  state.layout = schematic.layout;
  state.scene = schematic.scene || null;
  state.sceneById = indexScene(state.scene);
  state.svg = schematic.svg;
  state.current = payload.current || state.current;
  state.expected = payload.expected || null;
  state.drag = null;
  state.suppressClick = false;
  clearSelection();
  canvas.innerHTML = state.svg || "";
  bindSvg();
  renderDiagnostics(payload.diagnostics || []);
  renderExpected(payload.expected || null);
  updateCurrentCircuitLabel();
  if (options.fit !== false) fitSchematic();
}

async function rerouteCurrentLayout() {
  const button = document.querySelector("#reroute");
  statusEl.textContent = "Rerouting...";
  button.disabled = true;
  const started = performance.now();
  try {
    if (!state.layout) throw new Error("layout is not loaded yet");
    const routed = await fetchJson("/api/layout/autoroute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layout: state.layout })
    }, 20000);
    applySchematic(routed);
    const elapsed = ((performance.now() - started) / 1000).toFixed(1);
    statusEl.textContent = `Rerouted current layout in ${elapsed}s`;
  } catch (err) {
    statusEl.textContent = `Reroute failed: ${err.message}`;
  } finally {
    button.disabled = false;
  }
}

async function fetchJson(url, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(text || `${response.status} ${response.statusText}`);
    }
    return text ? JSON.parse(text) : {};
  } catch (err) {
    if (err.name === "AbortError") throw new Error(`request timed out after ${Math.round(timeoutMs / 1000)}s`);
    throw err;
  } finally {
    clearTimeout(timeout);
  }
}

function renderLibrary(items) {
  const groups = {};
  for (const item of items) (groups[item.category] ||= []).push(item);
  document.querySelector("#library").innerHTML = Object.entries(groups).map(([category, comps]) =>
    `<div class="category"><strong>${category}</strong>${comps.map(c => `<div class="component-type" data-id="${c.id}">${c.id}</div>`).join("")}</div>`
  ).join("");
}

function renderDiagnostics(diags) {
  document.querySelector("#diagnostics").innerHTML = diags.length ? diags.map(d =>
    `<div class="diag ${d.severity}">${d.severity}${d.code ? ` ${escapeHtml(d.code)}` : ""}: ${escapeHtml(d.message)}</div>`
  ).join("") : `<div class="diag">No validation errors</div>`;
}

function renderExpected(expected) {
  const el = document.querySelector("#expected-results");
  if (!el) return;
  if (!expected) {
    el.innerHTML = `<div class="diag">No expected-result metadata</div>`;
    return;
  }
  const expectedCodes = expected.codes || [];
  const actualCodes = expected.actual_codes || [];
  const status = expected.match === null || expected.match === undefined ? "N/A" : expected.match ? "MATCH" : "MISMATCH";
  el.innerHTML = `
    <div class="diag ${expected.match ? "" : "WARNING"}"><strong>Status:</strong> ${status}</div>
    <div class="prop-row"><strong>Expected</strong><br>${expectedCodes.length ? expectedCodes.map(escapeHtml).join("<br>") : "Expected clean result"}</div>
    <div class="prop-row"><strong>Actual</strong><br>${actualCodes.length ? actualCodes.map(escapeHtml).join("<br>") : "Actual clean result"}</div>
  `;
}

function updateCurrentCircuitLabel() {
  const label = document.querySelector("#current-circuit");
  const current = state.current;
  const name = current?.current_circuit_name || state.circuit?.name || "Circuit";
  const file = current?.source_filename || "";
  label.textContent = `Current circuit: ${name}${file ? ` / ${file}` : ""}`;
}

function bindSvg() {
  const svg = document.querySelector("#schematic");
  if (!svg) return;
  svg.classList.toggle("grid-hidden", !document.querySelector("#grid-toggle").checked);
  svg.addEventListener("wheel", onWheel, { passive: false });
  svg.addEventListener("pointerdown", onPointerDown);
  svg.addEventListener("pointermove", onPointerMove);
  svg.addEventListener("pointerup", onPointerUp);
  svg.addEventListener("pointerleave", onPointerUp);
  svg.addEventListener("click", onSvgClick);
  svg.querySelectorAll("[data-kind='pin']").forEach(el => {
    el.addEventListener("mouseenter", () => statusEl.textContent = `${el.dataset.ref}.${el.dataset.pinName} ${el.dataset.electricalType}`);
  });
}

function svgPoint(evt) {
  const svg = document.querySelector("#schematic");
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX; pt.y = evt.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}

function screenCtmInverse(svg) {
  const ctm = svg?.getScreenCTM?.();
  if (!ctm) return null;
  const inverse = ctm.inverse();
  if (![inverse.a, inverse.b, inverse.c, inverse.d].every(Number.isFinite)) return null;
  return { a: inverse.a, b: inverse.b, c: inverse.c, d: inverse.d };
}

function clientDeltaToSvgDelta(evt, drag) {
  const dx = evt.clientX - drag.startClientX;
  const dy = evt.clientY - drag.startClientY;
  return {
    x: dx * drag.clientToSvg.a + dy * drag.clientToSvg.c,
    y: dx * drag.clientToSvg.b + dy * drag.clientToSvg.d
  };
}

function onWheel(evt) {
  evt.preventDefault();
  const svg = document.querySelector("#schematic");
  const vb = svg.viewBox.baseVal;
  const factor = evt.deltaY < 0 ? 0.9 : 1.1;
  const p = svgPoint(evt);
  vb.x = p.x - (p.x - vb.x) * factor;
  vb.y = p.y - (p.y - vb.y) * factor;
  vb.width *= factor;
  vb.height *= factor;
}

function onPointerDown(evt) {
  if (evt.button !== undefined && evt.button !== 0) return;
  const sceneTarget = sceneElementForTarget(evt.target);
  const component = draggableComponentForTarget(evt.target, sceneTarget);
  const svg = document.querySelector("#schematic");
  const clientToSvg = screenCtmInverse(svg);
  if (!clientToSvg) {
    statusEl.textContent = "Drag ignored: SVG coordinate transform is not ready";
    return;
  }
  const p = svgPoint(evt);
  if (component) {
    const ref = component.dataset.ref;
    const placement = state.layout?.components?.[ref] || {};
    const currentTranslate = readTranslate(component);
    const sceneOriginX = finiteNumber(component.dataset.placementX, finiteNumber(placement.x, 0) - currentTranslate.x);
    const sceneOriginY = finiteNumber(component.dataset.placementY, finiteNumber(placement.y, 0) - currentTranslate.y);
    const layoutStartX = finiteNumber(placement.x, sceneOriginX + currentTranslate.x);
    const layoutStartY = finiteNumber(placement.y, sceneOriginY + currentTranslate.y);
    capturePointer(svg, evt.pointerId);
    state.drag = {
      type: "component",
      ref,
      group: component,
      captureTarget: svg,
      pointerId: evt.pointerId,
      pointerStart: p,
      startClientX: evt.clientX,
      startClientY: evt.clientY,
      clientToSvg,
      sceneOriginX,
      sceneOriginY,
      transformStartX: currentTranslate.x,
      transformStartY: currentTranslate.y,
      layoutStartX,
      layoutStartY,
      pending: true,
      active: false,
      moved: false
    };
    evt.preventDefault();
  } else if (sceneTarget) {
    state.drag = null;
  } else {
    const vb = svg.viewBox.baseVal;
    capturePointer(svg, evt.pointerId);
    state.drag = { type: "pan", captureTarget: svg, pointerId: evt.pointerId, startClientX: evt.clientX, startClientY: evt.clientY, x: vb.x, y: vb.y, w: vb.width, h: vb.height };
    evt.preventDefault();
  }
}

function onPointerMove(evt) {
  if (!state.drag) return;
  const svg = document.querySelector("#schematic");
  if (state.drag.type === "pan") {
    const vb = svg.viewBox.baseVal;
    vb.x = state.drag.x - (evt.clientX - state.drag.startClientX) * vb.width / svg.clientWidth;
    vb.y = state.drag.y - (evt.clientY - state.drag.startClientY) * vb.height / svg.clientHeight;
    return;
  }
  if (state.drag.pending) {
    const pixelDistance = Math.hypot(evt.clientX - state.drag.startClientX, evt.clientY - state.drag.startClientY);
    if (pixelDistance < DRAG_THRESHOLD_PX) return;
    state.drag.pending = false;
    state.drag.active = true;
    state.drag.moved = true;
  }
  const delta = clientDeltaToSvgDelta(evt, state.drag);
  const snap = document.querySelector("#snap-toggle").checked ? (state.layout.canvas.grid || 20) : 1;
  const nx = Math.round((state.drag.layoutStartX + delta.x) / snap) * snap;
  const ny = Math.round((state.drag.layoutStartY + delta.y) / snap) * snap;
  if (!Number.isFinite(nx) || !Number.isFinite(ny)) {
    statusEl.textContent = `Ignored invalid drag for ${state.drag.ref}`;
    return;
  }
  state.layout.components[state.drag.ref] = { ...(state.layout.components[state.drag.ref] || {}), x: nx, y: ny };
  if (state.current) state.current.layout_dirty = true;
  const group = document.querySelector(`#component-${cssSafe(state.drag.ref)}`);
  group?.setAttribute("transform", `translate(${formatNumber(nx - state.drag.sceneOriginX)},${formatNumber(ny - state.drag.sceneOriginY)})`);
  statusEl.textContent = `${state.drag.ref} ${nx}, ${ny}`;
}

function onPointerUp(evt = {}) {
  const drag = state.drag;
  if (!drag) return;
  releasePointer(drag.captureTarget, drag.pointerId);
  state.drag = null;
  if (drag.type === "component" && !drag.active && !drag.moved && evt.type === "pointerup") {
    suppressNextClick();
    selectComponentByRef(drag.ref);
    return;
  }
  if (drag.type === "component" && drag.active) {
    suppressNextClick();
  }
}

function suppressNextClick() {
  state.suppressClick = true;
  setTimeout(() => state.suppressClick = false, 120);
}

function onSvgClick(evt) {
  if (state.suppressClick) {
    evt.preventDefault();
    return;
  }
  const target = semanticSelectionTarget(evt.target);
  if (!target) return;
  evt.stopPropagation();
  selectResolvedTarget(target.el, target.sceneElement);
}

function selectSceneElement(evt) {
  if (state.suppressClick) return;
  const target = semanticSelectionTarget(evt.currentTarget || evt.target);
  if (!target) return;
  evt.stopPropagation();
  selectResolvedTarget(target.el, target.sceneElement);
}

function selectResolvedTarget(el, sceneElement = {}) {
  if (sceneElement.kind === "pin" || el.dataset.kind === "pin") return selectPinElement(el, sceneElement);
  if (sceneElement.net_name || el.dataset.net) return selectNetElement(el, sceneElement);
  const componentRef = sceneElement.component_ref || el.dataset.componentRef || el.dataset.ref || el.closest?.(".component")?.dataset.ref;
  if (componentRef) return selectComponentByRef(componentRef);
}

function semanticSelectionTarget(target) {
  const pin = target.closest?.("[data-kind='pin']");
  if (pin) return { el: pin, sceneElement: sceneElementForElement(pin) || {} };
  const net = target.closest?.("[data-net], [data-kind='wire'], [data-kind='wire_stub'], [data-kind='junction'], [data-kind='net_label'], [data-kind='net_label_endpoint'], [data-kind='power_symbol'], [data-kind='ground_symbol'], [data-kind='power_label'], [data-kind='ground_label']");
  if (net && (net.dataset.net || sceneElementForElement(net)?.net_name)) return { el: net, sceneElement: sceneElementForElement(net) || {} };
  const component = target.closest?.("[data-kind='component_group'], .component, [data-component-ref]");
  if (component) {
    const group = component.closest?.("[data-kind='component_group'], .component") || component;
    return { el: group, sceneElement: sceneElementForElement(group) || {} };
  }
  return null;
}

function sceneElementForElement(el) {
  const semantic = el.closest?.("[data-scene-id]");
  return semantic ? state.sceneById.get(semantic.dataset.sceneId) || null : null;
}

async function selectComponentByRef(ref) {
  const group = document.querySelector(`#component-${cssSafe(ref)}`);
  const sceneElement = group ? sceneElementForElement(group) || {} : {};
  await selectComponentElement(group || { dataset: { ref } }, { ...sceneElement, component_ref: ref });
}

async function selectComponentElement(el, sceneElement = {}) {
  clearSelection();
  const ref = sceneElement.component_ref || el.dataset.componentRef || el.dataset.ref;
  document.querySelector(`#component-${cssSafe(ref)}`)?.classList.add("selected");
  state.selected = sceneElement.id || el.dataset.sceneId || ref;
  document.querySelector("#properties").innerHTML = rows({ Reference: ref, Status: "Loading component details..." });
  try {
    const detail = await fetchJson(`/api/circuit/component-details/${encodeURIComponent(ref)}?t=${Date.now()}`, { cache: "no-store" });
    renderComponentDetails(detail);
    statusEl.textContent = `Selected component ${ref}`;
  } catch (err) {
    const comp = state.circuit.components.find(c => c.ref === ref) || {};
    document.querySelector("#properties").innerHTML = rows({ Reference: comp.ref || ref, Type: comp.component_id || "", Status: err.message });
  }
}

function selectPinElement(el, sceneElement = {}) {
  clearSelection();
  const p = el.dataset;
  const ref = sceneElement.component_ref || p.ref;
  const pinName = sceneElement.pin_name || p.pinName;
  const pinNumber = sceneElement.pin_number || p.pinNumber;
  state.selected = sceneElement.id || el.dataset.sceneId || `${ref}.${pinName || pinNumber}`;
  document.querySelector("#properties").innerHTML = rows({ Component: ref, Pin: `${pinNumber} ${pinName}`, Type: p.electricalType || "", Net: findPinNet(ref, pinName) || "" });
}

async function selectNetElement(el, sceneElement = {}) {
  clearSelection();
  const net = sceneElement.net_name || el.dataset.net || el.closest("[data-net]")?.dataset.net;
  const group = document.querySelector(`#net-${cssSafe(net)}`);
  const style = group?.dataset.renderStyle || "local_wire";
  document.querySelectorAll(".net").forEach(n => n.classList.toggle("highlight", n.dataset.net === net));
  state.selected = sceneElement.id || el.dataset.sceneId || net;
  document.querySelector("#properties").innerHTML = rows({ Net: net, "Render style": style, Status: "Loading net details..." });
  try {
    const detail = await fetchJson(`/api/circuit/net-details/${encodeURIComponent(net)}?t=${Date.now()}`, { cache: "no-store" });
    renderNetDetails(detail);
  } catch (err) {
    document.querySelector("#properties").innerHTML = rows({ Net: net, "Render style": style, Status: err.message });
  }
  statusEl.textContent = `Selected net ${net}`;
}

function findPinNet(ref, pinName) {
  for (const net of state.circuit.nets) {
    if (net.pins.some(p => p.component_ref === ref && (p.pin_name === pinName || p.resolved_name === pinName))) return net.name;
  }
}

function rows(obj) {
  return Object.entries(obj).map(([k, v]) => `<div class="prop-row"><strong>${escapeHtml(k)}</strong><br>${escapeHtml(v ?? "")}</div>`).join("");
}

function renderComponentDetails(detail) {
  const params = Object.entries(detail.parameters || {}).map(([key, value]) => `${key}=${value}`).join(", ") || "none";
  const patterns = (detail.topology_patterns || []).map(pattern => pattern.type).join(", ") || "none";
  const notes = [detail.notes, detail.warnings].filter(Boolean).map(escapeHtml).join("<br>");
  document.querySelector("#properties").innerHTML = `
    <div class="prop-title">${escapeHtml(detail.ref)} ${escapeHtml(detail.name || detail.component_id)}</div>
    ${rows({
      Reference: detail.ref,
      "Component ID": detail.component_id,
      "Human name": detail.name || "",
      Category: detail.category,
      Summary: detail.summary || "",
      Function: detail.function || "",
      "Common use": detail.common_use || "",
      Value: detail.value || "",
      Role: detail.role || "",
      Package: detail.package || "",
      Orientation: detail.orientation ?? "",
      "Metadata status": detail.verified_status || "",
      "Topology role": detail.topology_role || "",
      "Topology patterns": patterns,
      Parameters: params
    })}
    ${notes ? `<div class="prop-row"><strong>Notes</strong><br>${notes}</div>` : ""}
    <div class="prop-row"><strong>Pins</strong>${pinTable(detail.pins || [])}</div>
  `;
}

function pinTable(pins) {
  if (!pins.length) return `<div class="muted">No pin metadata</div>`;
  return `
    <table class="pin-table">
      <thead><tr><th>#</th><th>Name</th><th>Type</th><th>Net</th><th>Description</th></tr></thead>
      <tbody>${pins.map(pin => `
        <tr>
          <td>${escapeHtml(pin.number)}</td>
          <td>${escapeHtml(pin.name)}</td>
          <td>${escapeHtml(pin.electrical_type)}</td>
          <td>${escapeHtml(pin.connected_net || "")}</td>
          <td>${escapeHtml(pin.description || "")}</td>
        </tr>
      `).join("")}</tbody>
    </table>
  `;
}

function renderNetDetails(detail) {
  const selected = detail.manual_override || "auto";
  const options = [
    ["auto", "Auto"],
    ["direct", "Direct wire"],
    ["label", "Net labels"],
    ["power_symbol", "Power symbols"]
  ];
  document.querySelector("#properties").innerHTML = `
    <div class="prop-title">Net ${escapeHtml(detail.net_name)}</div>
    ${rows({
      "Endpoint count": detail.endpoint_count,
      "Resolved style": detail.resolved_route_style,
      "Manual override": detail.manual_override || "auto",
      "Auto reason": detail.auto_reason || "",
      "Estimated direct length": detail.route_style_debug?.estimated_direct_length ?? "",
      "Estimated direct bends": detail.route_style_debug?.estimated_direct_bends ?? ""
    })}
    <div class="prop-row">
      <label for="route-style-select"><strong>Route style</strong></label><br>
      <select id="route-style-select" data-net="${escapeHtml(detail.net_name)}">
        ${options.map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("")}
      </select>
    </div>
    <div class="prop-row"><strong>Endpoints</strong><br>${(detail.endpoints || []).map(endpoint => `${escapeHtml(endpoint.component_ref)}.${escapeHtml(endpoint.pin_name)}`).join("<br>")}</div>
  `;
  document.querySelector("#route-style-select")?.addEventListener("change", evt => changeNetRouteStyle(evt.target.dataset.net, evt.target.value));
}

async function changeNetRouteStyle(net, routeStyle) {
  if (!state.layout) return;
  statusEl.textContent = `Changing ${net} route style...`;
  const payload = await fetchJson("/api/layout/net-route-style", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ net_name: net, route_style: routeStyle, layout: state.layout })
  }, 20000);
  applySchematic(payload, { fit: false });
  if (state.current) state.current.layout_dirty = true;
  statusEl.textContent = `${net} route style set to ${routeStyle}`;
}

function clearSelection() {
  document.querySelectorAll(".component.selected").forEach(e => e.classList.remove("selected"));
  document.querySelectorAll(".net.highlight").forEach(e => e.classList.remove("highlight"));
}

function indexScene(scene) {
  const map = new Map();
  for (const element of scene?.elements || []) map.set(element.id, element);
  return map;
}

function sceneElementForTarget(target) {
  const el = target.closest?.("[data-scene-id]");
  return el ? state.sceneById.get(el.dataset.sceneId) || null : null;
}

function draggableComponentForTarget(target, sceneElement) {
  if (sceneElement && NON_COMPONENT_DRAG_KINDS.has(sceneElement.kind)) return null;
  return target.closest?.("[data-kind='component_group'], .component") || null;
}

function readTranslate(el) {
  const matrix = el.transform?.baseVal?.consolidate?.()?.matrix;
  if (matrix && Number.isFinite(matrix.e) && Number.isFinite(matrix.f)) return { x: matrix.e, y: matrix.f };
  const attr = el.getAttribute("transform") || "";
  const match = attr.match(/translate\(\s*([-+]?\d*\.?\d+)(?:[,\s]+([-+]?\d*\.?\d+))?\s*\)/);
  return match ? { x: finiteNumber(match[1], 0), y: finiteNumber(match[2], 0) } : { x: 0, y: 0 };
}

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function formatNumber(value) {
  const number = Number.isFinite(value) ? value : 0;
  return Math.abs(number) < 0.0001 ? "0" : String(Number(number.toFixed(3)));
}

function capturePointer(target, pointerId) {
  try {
    target?.setPointerCapture?.(pointerId);
  } catch {
    // Pointer capture is helpful but not required for the drag model.
  }
}

function releasePointer(target, pointerId) {
  try {
    target?.releasePointerCapture?.(pointerId);
  } catch {
    // The browser may release capture automatically on pointerup.
  }
}

function cssSafe(value) { return CSS.escape(value); }

document.querySelector("#reload").addEventListener("click", async () => {
  statusEl.textContent = "Reloading...";
  try {
    const payload = await fetchJson("/api/circuit/reload", { method: "POST" }, 20000);
    if (!payload.success) {
      renderLoadErrors(payload);
      statusEl.textContent = "Reload found errors";
      return;
    }
    applySchematic(payload);
    statusEl.textContent = `Reloaded ${payload.circuit_name}`;
  } catch (err) {
    statusEl.textContent = `Reload failed: ${err.message}`;
  }
});
document.querySelector("#reroute").addEventListener("click", rerouteCurrentLayout);
document.querySelector("#save-layout").addEventListener("click", async () => {
  try {
    await fetchJson("/api/layout/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ layout: state.layout }) }, 20000);
    if (state.current) state.current.layout_dirty = false;
    statusEl.textContent = "Layout saved";
  } catch (err) {
    if (state.current) state.current.layout_dirty = true;
    statusEl.textContent = `Save failed: ${err.message}`;
  }
});
document.querySelector("#export-svg").addEventListener("click", () => window.open("/api/export/svg", "_blank"));
document.querySelector("#export-png").addEventListener("click", () => window.open("/api/export/png", "_blank"));
document.querySelector("#fit").addEventListener("click", () => {
  fitSchematic();
});
document.querySelector("#reset").addEventListener("click", () => loadAll({ fresh: true }));
document.querySelector("#grid-toggle").addEventListener("change", () => document.querySelector("#schematic")?.classList.toggle("grid-hidden", !document.querySelector("#grid-toggle").checked));
document.querySelector("#search").addEventListener("input", evt => {
  const q = evt.target.value.toLowerCase();
  document.querySelectorAll(".component").forEach(el => el.classList.toggle("search-hit", q && el.dataset.ref.toLowerCase().includes(q)));
  document.querySelectorAll(".net").forEach(el => el.classList.toggle("highlight", q && el.dataset.net.toLowerCase().includes(q)));
});

window.__circuitNetlistDebug = {
  dragThreshold: DRAG_THRESHOLD_PX,
  state: () => {
    const svg = document.querySelector("#schematic");
    const vb = svg?.viewBox?.baseVal;
    return {
      layout: state.layout,
      selected: state.selected,
      current: state.current,
      sceneElementCount: state.scene?.elements?.length || 0,
      viewBox: vb ? { x: vb.x, y: vb.y, width: vb.width, height: vb.height } : null,
      drag: state.drag
        ? {
            type: state.drag.type,
            ref: state.drag.ref,
            pending: state.drag.pending,
            active: state.drag.active,
            sceneOriginX: state.drag.sceneOriginX,
            sceneOriginY: state.drag.sceneOriginY,
            layoutStartX: state.drag.layoutStartX,
            layoutStartY: state.drag.layoutStartY,
            transformStartX: state.drag.transformStartX,
            transformStartY: state.drag.transformStartY,
            clientToSvg: state.drag.clientToSvg || null
          }
        : null
    };
  }
};

loadAll();

document.querySelector("#load-circuit").addEventListener("click", openLoadModal);
document.querySelectorAll(".tab").forEach(button => button.addEventListener("click", () => switchTab(button.dataset.tab)));
document.querySelector("#circuit-filter").addEventListener("input", renderCatalogLists);
document.querySelector("#local-cnet").addEventListener("change", onLocalFileSelected);
document.querySelector("#local-layout").addEventListener("change", evt => state.localLayoutFile = evt.target.files?.[0] || null);
document.querySelector("#load-local").addEventListener("click", loadLocalFile);
setupDragDrop();

async function openLoadModal() {
  document.querySelector("#load-modal").showModal();
  document.querySelector("#load-errors").innerHTML = "";
  await loadCatalog();
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.id === `tab-${name}`));
}

async function loadCatalog() {
  if (!state.catalog) {
    state.catalog = await fetchJson(`/api/circuits?t=${Date.now()}`, { cache: "no-store" });
  }
  renderCatalogLists();
}

function renderCatalogLists() {
  if (!state.catalog) return;
  const q = document.querySelector("#circuit-filter").value.toLowerCase();
  renderCaseList("#examples-list", state.catalog.examples || [], q);
  renderCaseList("#regression-good-list", state.catalog.regression?.good || [], q);
  renderCaseList("#regression-fault-list", state.catalog.regression?.faults || [], q);
}

function renderCaseList(selector, items, query) {
  const filtered = items.filter(item => matchesCase(item, query));
  document.querySelector(selector).innerHTML = filtered.length ? filtered.map(item => `
    <article class="case-card">
      <div><strong>${escapeHtml(item.title)}</strong> <span class="badge">${escapeHtml(item.type || item.kind)}</span></div>
      <div class="muted">${escapeHtml(item.path)}</div>
      <div>${escapeHtml(item.description || "")}</div>
      <div class="muted">${item.component_count} components, ${item.net_count} nets</div>
      <div class="codes">${(item.expected_codes || []).map(code => `<code>${escapeHtml(code)}</code>`).join(" ")}</div>
      <button type="button" data-case-id="${escapeHtml(item.id)}">Load</button>
    </article>
  `).join("") : `<div class="diag">No circuits match</div>`;
  document.querySelectorAll(`${selector} [data-case-id]`).forEach(button => {
    button.addEventListener("click", () => loadCase(button.dataset.caseId));
  });
}

function matchesCase(item, query) {
  if (!query) return true;
  return [item.title, item.path, item.description, item.family, item.kind, ...(item.expected_codes || [])].join(" ").toLowerCase().includes(query);
}

async function loadCase(caseId) {
  if (!confirmDiscardLayout()) return;
  statusEl.textContent = "Loading circuit...";
  const payload = await fetchJson("/api/circuit/load-case", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ case_id: caseId })
  }, 20000);
  if (!payload.success) {
    renderLoadErrors(payload);
    statusEl.textContent = `Could not load ${caseId}`;
    return;
  }
  applySchematic(payload);
  document.querySelector("#load-modal").close();
  statusEl.textContent = `Loaded ${payload.circuit_name}: ${payload.schematic.circuit.components.length} components, ${payload.schematic.circuit.nets.length} nets`;
}

async function onLocalFileSelected(evt) {
  state.localFile = evt.target.files?.[0] || null;
  if (!state.localFile) return;
  const text = await state.localFile.text();
  const lines = text.split(/\r?\n/).slice(0, 60).join("\n");
  const name = (text.match(/^\s*CIRCUIT\s+([A-Za-z_][A-Za-z0-9_]*)/m) || [])[1] || "unknown";
  document.querySelector("#file-preview").textContent = `${state.localFile.name} (${state.localFile.size} bytes)\nDetected circuit: ${name}\n\n${lines}`;
}

async function loadLocalFile() {
  if (!confirmDiscardLayout()) return;
  if (!state.localFile) {
    renderLoadErrors({ diagnostics: [{ severity: "ERROR", message: "Choose a .cnet file first." }] });
    return;
  }
  if (!state.localFile.name.toLowerCase().endsWith(".cnet")) {
    renderLoadErrors({ diagnostics: [{ severity: "ERROR", message: "Only .cnet files are supported." }] });
    return;
  }
  const text = await state.localFile.text();
  const layoutText = state.localLayoutFile ? await state.localLayoutFile.text() : null;
  const payload = await fetchJson("/api/circuit/load-text", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: state.localFile.name, text, layout_text: layoutText })
  }, 20000);
  if (!payload.success) {
    renderLoadErrors(payload);
    statusEl.textContent = `Could not load ${state.localFile.name}`;
    return;
  }
  applySchematic(payload);
  document.querySelector("#load-modal").close();
  statusEl.textContent = `Loaded ${payload.filename}`;
}

function confirmDiscardLayout() {
  if (!state.current?.layout_dirty) return true;
  return confirm("Current layout has unsaved changes. Load another circuit anyway?");
}

function renderLoadErrors(payload) {
  const findings = payload.diagnostics || payload.validation || [];
  document.querySelector("#load-errors").innerHTML = findings.length ? findings.map(d =>
    `<div class="diag ${d.severity || "ERROR"}">${escapeHtml(d.severity || "ERROR")}${d.code ? ` ${escapeHtml(d.code)}` : ""}: ${escapeHtml(d.message || String(d))}</div>`
  ).join("") : `<div class="diag ERROR">Load failed</div>`;
}

function setupDragDrop() {
  const overlay = document.querySelector("#drop-overlay");
  ["dragenter", "dragover"].forEach(name => canvas.addEventListener(name, evt => {
    evt.preventDefault();
    overlay.classList.add("visible");
  }));
  ["dragleave", "drop"].forEach(name => canvas.addEventListener(name, evt => {
    evt.preventDefault();
    if (name === "dragleave") overlay.classList.remove("visible");
  }));
  canvas.addEventListener("drop", async evt => {
    overlay.classList.remove("visible");
    const files = [...(evt.dataTransfer?.files || [])];
    const cnet = files.find(file => file.name.toLowerCase().endsWith(".cnet"));
    const layout = files.find(file => file.name.toLowerCase().endsWith(".layout.json") || file.name.toLowerCase().endsWith(".json"));
    if (!cnet) {
      statusEl.textContent = "Drop a .cnet file to load a circuit";
      return;
    }
    state.localFile = cnet;
    state.localLayoutFile = layout || null;
    await loadLocalFile();
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function fitSchematic() {
  const svg = document.querySelector("#schematic");
  const content = svg?.querySelector("#wires, #components");
  if (!svg || !content) return;
  let box;
  try {
    box = svg.querySelector("#wires").getBBox();
    const componentBox = svg.querySelector("#components").getBBox();
    const minX = Math.min(box.x, componentBox.x);
    const minY = Math.min(box.y, componentBox.y);
    const maxX = Math.max(box.x + box.width, componentBox.x + componentBox.width);
    const maxY = Math.max(box.y + box.height, componentBox.y + componentBox.height);
    box = { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
  } catch {
    return;
  }
  if (!box.width || !box.height) return;
  const pad = Math.max(box.width, box.height) * 0.08;
  svg.setAttribute("viewBox", `${box.x - pad} ${box.y - pad} ${box.width + pad * 2} ${box.height + pad * 2}`);
}
