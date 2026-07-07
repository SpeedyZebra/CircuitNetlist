const { test, expect } = require("@playwright/test");

const CENTER_FLASH_TOLERANCE_PX = 180;
const PATH_TOLERANCE_PX = 170;
const STEP_TOLERANCE_PX = 150;

test.beforeEach(async ({ page }) => {
  page.on("pageerror", error => {
    throw error;
  });
  await page.goto("/");
  await waitForSchematic(page);
});

test("component clicks update properties panel and browser debug state", async ({ page }) => {
  await clickComponent(page, "BAT1");

  await expect(page.locator("#properties")).toContainText("BAT1");
  await expect(page.locator("#properties")).toContainText("Pins");
  await expect(page.locator("#status")).toContainText(/Clicked component BAT1|Selected component BAT1 from pointerup/);

  const clickDebug = await page.evaluate(() => ({
    lastClick: window.__circuitNetlistDebug.lastClick,
    lastSelection: window.__circuitNetlistDebug.lastSelection,
    lastPropertiesHtml: window.__circuitNetlistDebug.lastPropertiesHtml
  }));
  expect(clickDebug.lastClick?.resolvedSelectionType).toBe("component");
  expect(clickDebug.lastClick?.resolvedComponentRef).toBe("BAT1");
  expect(clickDebug.lastClick?.propertiesUpdated).toBe(true);
  expect(clickDebug.lastSelection?.type).toBe("component");
  expect(clickDebug.lastSelection?.componentRef).toBe("BAT1");
  expect(clickDebug.lastPropertiesHtml).toContain("BAT1");

  await page.locator("#component-CHG1 .hit-area").click({ position: { x: 8, y: 8 } });
  await expect(page.locator("#properties")).toContainText("CHG1");
  await expect(page.locator("#properties")).toContainText("Pins");

  await page.evaluate(() => window.__circuitNetlistDebug.selectComponentByRef("U1"));
  await expect(page.locator("#properties")).toContainText("U1");
  await expect(page.locator("#properties")).toContainText("Pins");
  await expect(page.locator("#status")).toContainText("Selected component U1 from debug hook");

  const hookDebug = await page.evaluate(() => window.__circuitNetlistDebug.lastSelection);
  expect(hookDebug?.type).toBe("component");
  expect(hookDebug?.componentRef).toBe("U1");
  expect(hookDebug?.detailLoaded).toBe(true);
});

test("battery click and below-threshold movement do not move it", async ({ page }, testInfo) => {
  const before = await snapshot(page, "BAT1");
  await clickComponent(page, "BAT1");
  const afterClick = await snapshot(page, "BAT1");
  await attachDiagnostics(testInfo, page, "battery-click", [before, afterClick]);

  expect(await isSelected(page, "BAT1")).toBe(true);
  expect(distance(center(before.box), center(afterClick.box))).toBeLessThan(1.5);

  const threshold = await page.evaluate(() => window.__circuitNetlistDebug.dragThreshold);
  await dragComponent(page, "BAT1", threshold - 2, 1, { steps: 1, validatePath: false });
  const afterSmallMove = await snapshot(page, "BAT1");
  await attachDiagnostics(testInfo, page, "battery-threshold", [before, afterSmallMove]);

  expect(distance(center(before.box), center(afterSmallMove.box))).toBeLessThan(1.5);
  expect(await isSelected(page, "BAT1")).toBe(true);
});

test("battery first drag follows path without center flash and later drags are cumulative", async ({ page }, testInfo) => {
  const first = await dragComponent(page, "BAT1", 80, 40, { steps: 8 });
  await attachDiagnostics(testInfo, page, "battery-first-drag", first.samples);
  expectPath(first, 80, 40);
  expectNoCenterFlash(first);

  const firstFinal = first.final;
  const second = await dragComponent(page, "BAT1", 90, 45, { steps: 8 });
  await attachDiagnostics(testInfo, page, "battery-second-drag", second.samples);
  expect(distance(center(second.initial.box), center(firstFinal.box))).toBeLessThan(3);
  expectPath(second, 90, 45);
  expectNoCenterFlash(second);

  const third = await dragComponent(page, "BAT1", -70, -50, { steps: 8 });
  await attachDiagnostics(testInfo, page, "battery-third-drag", third.samples);
  expect(distance(center(third.initial.box), center(second.final.box))).toBeLessThan(3);
  expectPath(third, -70, -50);
  expectNoCenterFlash(third);

  expectFiniteTransform(first.final.transform);
  expectFiniteTransform(second.final.transform);
  expectFiniteTransform(third.final.transform);
  expect(await isVisibleComponent(page, "BAT1")).toBe(true);
});

test("resistor first and second drags are cumulative without exposing hidden body", async ({ page }, testInfo) => {
  await expect(page.locator("#component-R_LED [data-kind='component_body']")).toHaveCount(0);

  const first = await dragComponent(page, "R_LED", 70, 30, { steps: 6 });
  await attachDiagnostics(testInfo, page, "resistor-first-drag", first.samples);
  expectPath(first, 70, 30);
  expectFiniteTransform(first.final.transform);
  await expect(page.locator("#component-R_LED [data-kind='component_body']")).toHaveCount(0);

  const second = await dragComponent(page, "R_LED", -50, -40, { steps: 6 });
  await attachDiagnostics(testInfo, page, "resistor-second-drag", second.samples);
  expect(distance(center(second.initial.box), center(first.final.box))).toBeLessThan(3);
  expectPath(second, -50, -40);
  expectFiniteTransform(second.final.transform);
  await expect(page.locator("#component-R_LED [data-kind='component_body']")).toHaveCount(0);
});

test("reload followed by immediate first battery drag is stable", async ({ page }, testInfo) => {
  await page.locator("#reload").click();
  await waitForSchematic(page);
  const result = await dragComponent(page, "BAT1", 80, 40, { steps: 8 });
  await attachDiagnostics(testInfo, page, "battery-reload-first-drag", result.samples);
  expectPath(result, 80, 40);
  expectNoCenterFlash(result);
  expectFiniteTransform(result.final.transform);
});

async function waitForSchematic(page) {
  await page.locator("#schematic[data-scene-version='1.0']").waitFor();
  await page.locator("#component-BAT1[data-kind='component_group']").waitFor();
  await expect.poll(() => page.evaluate(() => window.__circuitNetlistDebug?.state().sceneElementCount || 0)).toBeGreaterThan(0);
}

async function clickComponent(page, ref) {
  const box = await visibleBox(page, ref);
  const point = center(box);
  await page.mouse.move(point.x, point.y);
  await page.mouse.down();
  await page.mouse.up();
}

async function dragComponent(page, ref, dx, dy, options = {}) {
  const steps = options.steps || 8;
  const initial = await snapshot(page, ref);
  const start = center(initial.box);
  const samples = [initial];

  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  for (let index = 1; index <= steps; index += 1) {
    await page.mouse.move(start.x + (dx * index) / steps, start.y + (dy * index) / steps);
    samples.push(await snapshot(page, ref, index, steps, dx, dy));
  }
  await page.mouse.up();

  const final = await snapshot(page, ref, steps, steps, dx, dy);
  const result = { ref, dx, dy, steps, initial, samples, final };
  if (options.validatePath !== false) {
    expectPath(result, dx, dy);
    expectNoCenterFlash(result);
  }
  return result;
}

async function snapshot(page, ref, step = 0, steps = 1, dx = 0, dy = 0) {
  const locator = component(page, ref);
  const box = await locator.boundingBox();
  expect(box, `missing bounding box for ${ref}`).not.toBeNull();
  expect(box.width, `zero-width bounding box for ${ref}`).toBeGreaterThan(0);
  expect(box.height, `zero-height bounding box for ${ref}`).toBeGreaterThan(0);

  const transform = (await locator.getAttribute("transform")) || "";
  expectFiniteTransform(transform);
  const debug = await page.evaluate(componentRef => {
    const state = window.__circuitNetlistDebug.state();
    return {
      layout: state.layout?.components?.[componentRef] || null,
      viewBox: state.viewBox,
      drag: state.drag
    };
  }, ref);
  const svgBox = await page.locator("#schematic").boundingBox();
  const viewport = page.viewportSize();
  return {
    ref,
    step,
    steps,
    expectedDx: dx,
    expectedDy: dy,
    box,
    center: center(box),
    transform,
    debug,
    svgCenter: svgBox ? { x: svgBox.x + svgBox.width / 2, y: svgBox.y + svgBox.height / 2 } : null,
    viewportCenter: viewport ? { x: viewport.width / 2, y: viewport.height / 2 } : null
  };
}

async function visibleBox(page, ref) {
  const box = await component(page, ref).boundingBox();
  expect(box, `missing bounding box for ${ref}`).not.toBeNull();
  return box;
}

function component(page, ref) {
  return page.locator(`#component-${cssEscape(ref)}[data-kind='component_group']`);
}

async function isSelected(page, ref) {
  return component(page, ref).evaluate(el => el.classList.contains("selected"));
}

async function isVisibleComponent(page, ref) {
  const box = await component(page, ref).boundingBox();
  return Boolean(box && box.width > 0 && box.height > 0);
}

function expectPath(result, dx, dy) {
  const start = center(result.initial.box);
  let previous = start;
  for (const sample of result.samples.slice(1)) {
    const fraction = sample.step / sample.steps;
    const expected = { x: start.x + dx * fraction, y: start.y + dy * fraction };
    const actual = center(sample.box);
    const expectedStep = distance(previous, expected);
    expect(distance(actual, expected), diagnostic(result, sample, expected)).toBeLessThan(PATH_TOLERANCE_PX);
    expect(distance(actual, previous), diagnostic(result, sample, expected)).toBeLessThan(expectedStep + STEP_TOLERANCE_PX);
    previous = actual;
  }

  const finalExpected = { x: start.x + dx, y: start.y + dy };
  expect(distance(center(result.final.box), finalExpected), diagnostic(result, result.final, finalExpected)).toBeLessThan(PATH_TOLERANCE_PX);
}

function expectNoCenterFlash(result) {
  const start = center(result.initial.box);
  for (const sample of result.samples.slice(1)) {
    for (const centerPoint of [sample.svgCenter, sample.viewportCenter].filter(Boolean)) {
      const fraction = sample.step / sample.steps;
      const expected = { x: start.x + result.dx * fraction, y: start.y + result.dy * fraction };
      const expectedDistance = distance(expected, centerPoint);
      const actualDistance = distance(center(sample.box), centerPoint);
      const deviatedFromPath = distance(center(sample.box), expected) > PATH_TOLERANCE_PX / 2;
      expect(
        actualDistance < expectedDistance - CENTER_FLASH_TOLERANCE_PX && deviatedFromPath,
        diagnostic(result, sample, expected)
      ).toBe(false);
    }
  }
}

function expectFiniteTransform(transform) {
  expect(transform).not.toMatch(/NaN|undefined|null|Infinity|-Infinity/);
}

async function attachDiagnostics(testInfo, page, name, samples) {
  const payload = {
    url: page.url(),
    samples
  };
  await testInfo.attach(`${name}.json`, {
    body: JSON.stringify(payload, null, 2),
    contentType: "application/json"
  });
}

function diagnostic(result, sample, expected) {
  return JSON.stringify(
    {
      ref: result.ref,
      step: sample.step,
      transform: sample.transform,
      expected,
      actual: center(sample.box),
      svgCenter: sample.svgCenter,
      viewportCenter: sample.viewportCenter,
      debug: sample.debug
    },
    null,
    2
  );
}

function center(box) {
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function cssEscape(value) {
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}
