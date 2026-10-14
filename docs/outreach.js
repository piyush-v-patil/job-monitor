/* Outreach: who to contact about a posting, and a message to send them.

   Loaded after app.js, and uses its helpers ($, esc, safeUrl, toast, T, data)
   rather than duplicating them. Each feed row gets a "Reach out" button that
   opens this panel for that posting.

   Two halves, because only one of them can be automated:

   - Who to contact. The tracker cannot know the hiring manager: LinkedIn shows
     its "Meet the hiring team" panel only to signed-in users, and no employer
     board publishes one. So the panel builds the searches you would run by
     hand - the posting, a people search for the manager, one for the recruiter
     - and makes them sharper once the draft has read the posting and named
     the team and the title the role reports to.

   - What to send. Gemini reads the posting itself (its url_context tool), reads
     your resume, and returns fit points, honest gaps, a LinkedIn note and an
     email - as JSON against a schema, so nothing has to be scraped out of
     prose.

   Nothing here touches the repo, which is public, as is the dashboard. The
   Gemini key and your resume live in this browser's localStorage and leave it
   only inside the request to Google, when you click Draft. Drafts are cached
   per posting in the same place, so reopening one costs nothing. */
(() => {
  const API = "https://generativelanguage.googleapis.com/v1beta/models/";
  // Tried in order. The first is Google's moving "latest Flash" alias; the
  // others take over when it answers 503 "high demand", which it does often
  // enough under load that a single model would make the button unreliable.
  const MODELS = ["gemini-flash-latest", "gemini-3.7-flash", "gemini-flash-lite-latest"];
  const TIMEOUT_MS = 100000;    // reading a posting has measured at ~60s under load
  const NOTE_LIMIT = 300;       // LinkedIn's limit on a connection note
  const PDF_MAX = 4 * 1024 * 1024;
  const CACHE_MAX = 80;         // drafts kept; the oldest go first

  const K = {
    key: "outreach_gemini_key", pdf: "outreach_resume_pdf",
    pdfName: "outreach_resume_name", text: "outreach_resume_text",
    index: "outreach_drafts", draft: id => "outreach_draft:" + id,
  };

  // localStorage can be absent or full (private mode, storage cleared), and
  // the panel must still open - a missing key just means copy-prompt mode.
  const get = k => { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } };
  const put = (k, v) => {
    try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); return true; }
    catch (e) { return false; }
  };

  let current = null;     // id of the posting the panel shows
  let inflight = null;    // AbortController of a running draft

  // ---- who to contact ------------------------------------------------------
  const EARLY = new Set(["intern", "newgrad", "entry"]);
  const people = q => "https://www.linkedin.com/search/results/people/?keywords=" +
    encodeURIComponent(q.replace(/\s+/g, " ").trim());

  function contacts(j, out) {
    const company = j.company || "";
    const manager = (out && out.manager_title) || T.managerTitle || "hiring manager";
    const team = (out && out.team) || "";
    // students and new grads are hired through university recruiting, which is
    // a different person from the recruiter on the experienced req
    const recruiter = EARLY.has(j.tier) ? "university recruiter"
      : (T.recruiterTitle || "recruiter");
    const li = [j.url, j.employer_url].map(safeUrl).find(u => /linkedin\.com/i.test(u));
    const quoted = [company, manager, team].filter(Boolean).map(s => `"${s}"`).join(" ");
    return [
      li
        ? { href: li, label: "The posting on LinkedIn",
            hint: "Signed in, look for “Meet the hiring team” - when the poster shows it, that is your person." }
        : { href: "https://www.linkedin.com/jobs/search/?keywords=" +
                  encodeURIComponent(`${j.title} ${company}`),
            label: "Find the posting on LinkedIn",
            hint: "If it is listed there too, its page may name the hiring team." },
      { href: people(`${company} ${manager} ${team}`), label: `${manager}${team ? `, ${team}` : ""}`,
        hint: `LinkedIn people at ${company} with that title.` },
      { href: people(`${company} ${recruiter}`), label: recruiter[0].toUpperCase() + recruiter.slice(1),
        hint: "Recruiters answer cold messages more often than managers do." },
      { href: "https://www.google.com/search?q=" + encodeURIComponent(`site:linkedin.com/in ${quoted}`),
        label: "Google the profiles", hint: "Often finds people LinkedIn's own search ranks low." },
    ];
  }

  // ---- the request ---------------------------------------------------------
  const SCHEMA = {
    type: "object",
    properties: {
      job_read: { type: "boolean" },
      team: { type: "string" },
      manager_title: { type: "string" },
      fit: { type: "array", items: { type: "string" } },
      gaps: { type: "array", items: { type: "string" } },
      linkedin_note: { type: "string" },
      email_subject: { type: "string" },
      email_body: { type: "string" },
    },
    required: ["job_read", "team", "manager_title", "fit", "gaps",
               "linkedin_note", "email_subject", "email_body"],
  };

  function brief(j, jd, attached) {
    const links = [j.employer_url, j.url].map(safeUrl).filter(Boolean);
    const role = [
      `Title: ${j.title}`, `Company: ${j.company}`,
      j.location ? `Location: ${j.location}` : "",
      links.length ? `Posting: ${links.join("  (also listed at)  ")}` : "",
    ].filter(Boolean).join("\n");
    const source = jd
      ? `The candidate pasted the job description:\n"""\n${jd}\n"""`
      : links.length
        ? "Read the posting at the URL above before writing anything."
        : "There is no link to the posting, so work from the title alone.";
    const resume = attached
      ? "Their resume is attached."
      : "Their resume follows:\n\"\"\"\n" + get(K.text) + "\n\"\"\"";
    return `You are helping a job seeker contact the people hiring for one role.

THE ROLE
${role}

${source}
Set job_read to true only if you actually read the job description, from the URL or the pasted text. If you could not, set it to false and work from the title alone.

THE CANDIDATE
${resume}
The resume is the only source of facts about the candidate. Never invent an employer, project, number, skill or credential that is not in it, and never round a number up. Keep every fact with the job or project it is listed under: a skill used on a project was not used at an employer, and a metric from one role never moves to another.

WRITE
- fit: the 3 strongest, most specific matches between the resume and what the posting asks for, one sentence each, in the form "<what the posting asks for>: <the resume evidence, naming the role or project it comes from>".
- gaps: requirements in the posting the resume does not show, most important first, at most 3, or none. Plain and factual - these are for the candidate, not the recipient.
- team: the team or product area as the posting names it, or "" if it does not.
- manager_title: the title of the person this role most likely reports to; "${T.managerTitle || "hiring manager"}" if nothing more specific can be inferred.
- linkedin_note: a LinkedIn connection note of at most 280 characters including spaces. Start with "Hi [Name],". Name the role, give one concrete resume fact that matches the posting, end with a light ask.
- email_subject: under 9 words, naming the role.
- email_body: under 130 words. Start with "Hi [Name],". Open with the specific reason this role fits, never with "I am writing to". One short paragraph of evidence drawn from the fit points, then a one-line ask for a short call. Sign off with the candidate's name from the resume.

STYLE
Plain text, no markdown, no emoji. Specific and direct. No filler such as "I am excited", "passionate", "I would love to", "I came across". Do not mention visas, sponsorship, salary or start dates.`;
  }

  function request(j, jd) {
    const pdf = get(K.pdf);
    const parts = [];
    if (pdf) parts.push({ inline_data: { mime_type: "application/pdf", data: pdf } });
    parts.push({ text: brief(j, jd, !!pdf) });
    const body = {
      contents: [{ role: "user", parts }],
      generationConfig: { responseMimeType: "application/json", responseSchema: SCHEMA },
    };
    // Pasted text needs no fetching, and fetching is most of the wait.
    if (!jd && [j.employer_url, j.url].some(safeUrl)) body.tools = [{ url_context: {} }];
    return body;
  }

  // Answer -> {out, read, model}. Thought parts are the model's reasoning, not
  // its answer, so they are dropped before the JSON is parsed.
  function parse(resp, model) {
    const c = (resp.candidates || [])[0];
    if (!c) {
      const why = resp.promptFeedback && resp.promptFeedback.blockReason;
      throw new Error(why ? `Gemini declined the request (${why}).` : "Gemini returned no answer.");
    }
    const text = ((c.content && c.content.parts) || [])
      .filter(p => !p.thought && typeof p.text === "string").map(p => p.text).join("");
    let out;
    try { out = JSON.parse(text); }
    catch (e) {
      throw new Error(c.finishReason === "MAX_TOKENS"
        ? "The answer was cut off - try again." : "Gemini's answer was not readable - try again.");
    }
    // url_context reports per URL whether it got the page; the model's own
    // job_read says whether it got a description out of it. Both must hold.
    const urls = (c.urlContextMetadata && c.urlContextMetadata.urlMetadata) || [];
    const fetched = urls.some(u => /SUCCESS/.test(u.urlRetrievalStatus || ""));
    // "no gaps" sometimes arrives as [""], which would render as an empty bullet
    const items = a => (Array.isArray(a) ? a : []).map(s => String(s).trim()).filter(Boolean);
    out.fit = items(out.fit);
    out.gaps = items(out.gaps);
    return { out, read: !!out.job_read && (fetched || !urls.length), model };
  }

  // 503 (busy), 429 (rate limit), 500s and timeouts move on to the next model;
  // anything else - a bad key above all - is the same on every model, so stop.
  async function draft(j, jd, onStep) {
    const key = get(K.key);
    let last = null;
    for (const model of MODELS) {
      onStep(model);
      const ctl = new AbortController();
      inflight = ctl;
      const timer = setTimeout(() => ctl.abort("timeout"), TIMEOUT_MS);
      try {
        const r = await fetch(API + model + ":generateContent", {
          method: "POST", signal: ctl.signal,
          headers: { "Content-Type": "application/json", "X-goog-api-key": key },
          body: JSON.stringify(request(j, jd)),
        });
        const body = await r.json().catch(() => ({}));
        if (r.ok) return parse(body, model);
        const msg = (body.error && body.error.message) || r.statusText;
        last = new Error(`${model}: ${msg}`);
        if (![429, 500, 502, 503, 504].includes(r.status)) {
          throw new Error(r.status === 400 || r.status === 403
            ? `Gemini rejected the key or the request: ${msg}` : msg);
        }
      } catch (e) {
        if (ctl.signal.aborted && ctl.signal.reason !== "timeout") throw new Error("cancelled");
        if (!ctl.signal.aborted && !(e instanceof TypeError)) throw e;   // not a network failure
        last = new Error(ctl.signal.aborted ? `${model} took too long` : `${model}: ${e.message}`);
      } finally {
        clearTimeout(timer);
        if (inflight === ctl) inflight = null;
      }
    }
    throw new Error("Gemini is busy on every model right now - try again in a minute. (" +
                    (last ? last.message : "no answer") + ")");
  }

  // ---- drafts kept per posting ---------------------------------------------
  function loadDraft(id) {
    try { return JSON.parse(get(K.draft(id)) || "null"); } catch (e) { return null; }
  }
  function saveDraft(id, d) {
    let index = [];
    try { index = JSON.parse(get(K.index) || "[]"); } catch (e) { /* start over */ }
    index = index.filter(x => x !== id);
    index.push(id);
    while (index.length > CACHE_MAX) put(K.draft(index.shift()), "");
    put(K.draft(id), JSON.stringify(d));
    put(K.index, JSON.stringify(index));
  }

  // ---- the panel -----------------------------------------------------------
  const ready = () => !!get(K.key) && !!(get(K.pdf) || get(K.text).trim());
  const field = (id, value, rows) =>
    `<textarea id="${id}" rows="${rows}" spellcheck="true">${esc(value)}</textarea>`;

  function render(note) {
    const j = data && data.jobs[current];
    if (!j) return;
    const d = loadDraft(current);
    const out = d && d.out;
    const href = safeUrl(j.employer_url) || safeUrl(j.url);
    const list = items => items.length
      ? `<ul>${items.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`
      : `<p class="or-dim">None that the posting states.</p>`;

    $("orBody").innerHTML = `
      <p class="or-job">${href
        ? `<a href="${href}" target="_blank" rel="noopener">${esc(j.title)}</a>` : esc(j.title)}
        <span>${esc(j.company)}${j.location ? ` · ${esc(j.location)}` : ""}</span></p>

      <section class="or-sec">
        <h4>Who to contact</h4>
        <ul class="or-links">${contacts(j, out).map(c => `
          <li><a href="${esc(c.href)}" target="_blank" rel="noopener">${esc(c.label)} ↗</a>
              <span>${esc(c.hint)}</span></li>`).join("")}
        </ul>
        ${out ? "" : `<p class="or-dim">These get sharper after a draft: it names the team and the title the role reports to.</p>`}
      </section>

      <section class="or-sec">
        <h4>Message</h4>
        <details id="orJdBox"${d && !d.read ? " open" : ""}>
          <summary>Paste the job description${d && !d.read ? " - the last draft could not read the posting" : " (optional; faster, and needed when a page can't be read)"}</summary>
          ${field("orJd", "", 5)}
        </details>
        <div class="or-act">
          ${ready()
            ? `<button class="active" data-or="draft">${out ? "↻ Redraft" : "✨ Draft with Gemini"}</button>`
            : `<button data-or="setup">Set up Gemini first</button>`}
          <button data-or="prompt" title="Copy the same instructions to paste into Gemini, ChatGPT or Claude yourself, with your resume attached">⧉ Copy as a prompt</button>
          <span id="orStatus" class="or-dim">${esc(note || (d
            ? `Drafted ${new Date(d.at).toLocaleDateString()} with ${d.model}.` : ""))}</span>
        </div>
        ${d && !d.read ? `<p class="or-warn">Gemini could not read this posting, so the draft below is from the title alone. Paste the description above and redraft.</p>` : ""}
        ${out ? `
        <div class="or-two">
          <div><h5>Why you fit</h5>${list(out.fit)}</div>
          <div><h5>Gaps to be ready for</h5>${list(out.gaps)}</div>
        </div>
        <h5>LinkedIn note <span id="orCount" class="or-dim"></span></h5>
        ${field("orNote", out.linkedin_note, 3)}
        <div class="or-act"><button data-or="copy" data-from="orNote">Copy note</button>
          <span class="or-dim">Replace [Name], and check each claim against your resume - the
            model can attach a skill to the wrong job.</span></div>
        <h5>Email</h5>
        <input type="text" id="orSubject" value="${esc(out.email_subject)}">
        ${field("orEmail", out.email_body, 9)}
        <div class="or-act"><button data-or="copy" data-from="orEmail">Copy email</button>
          <button data-or="mail">Open in mail app</button></div>` : ""}
      </section>

      <details class="or-sec" id="orSetup"${ready() ? "" : " open"}>
        <summary><h4>Gemini key &amp; resume</h4></summary>
        <p class="or-dim">Kept in this browser only and sent only to Google's Gemini API, when you
          click Draft. Never written to the repo. In Google AI Studio you can restrict the key to
          <b>${esc(location.origin)}</b> so it is useless anywhere else.</p>
        <label for="orKey">Gemini API key</label>
        <input id="orKey" type="password" autocomplete="off" placeholder="AIza... or AQ...">
        <label for="orPdf">Resume (PDF)</label>
        <div class="or-act"><input id="orPdf" type="file" accept="application/pdf">
          <span class="or-dim">${get(K.pdf)
            ? `Stored: ${esc(get(K.pdfName) || "resume.pdf")} <button data-or="droppdf">Remove</button>` : "None stored"}</span></div>
        <label for="orText">…or paste it as text (used when no PDF is stored)</label>
        ${field("orText", get(K.text), 4)}
        <div class="or-act"><button class="active" data-or="save">Save</button>
          <button data-or="forget">Forget key &amp; resume</button></div>
      </details>`;

    $("orKey").value = get(K.key);
    count();
  }

  function count() {
    const n = $("orNote") ? $("orNote").value.length : 0;
    if (!$("orCount")) return;
    $("orCount").textContent = `${n}/${NOTE_LIMIT}`;
    $("orCount").classList.toggle("over", n > NOTE_LIMIT);
  }

  function status(msg) { if ($("orStatus")) $("orStatus").textContent = msg; }

  async function runDraft() {
    const id = current, j = data.jobs[id];
    const jd = ($("orJd") && $("orJd").value.trim()) || "";
    const btn = $("ordlg").querySelector('[data-or="draft"]');
    if (btn) btn.disabled = true;
    try {
      const res = await draft(j, jd, model =>
        status(`Asking ${model}… ${jd ? "" : "reading the posting can take up to a minute."}`));
      saveDraft(id, { at: Date.now(), model: res.model, read: res.read || !!jd, out: res.out });
      if (current === id && $("ordlg").open) render();
    } catch (e) {
      if (e.message === "cancelled") return;
      if (current === id) { status(e.message); if (btn) btn.disabled = false; }
    }
  }

  async function copy(text, what) {
    try { await navigator.clipboard.writeText(text); toast(`${what} copied`); }
    catch (e) { toast("Copy blocked by the browser - select the text and copy it"); }
  }

  function saveSetup() {
    const ok = put(K.key, $("orKey").value.trim()) && put(K.text, $("orText").value.trim());
    const f = $("orPdf").files && $("orPdf").files[0];
    if (!f) { toast(ok ? "Saved" : "This browser would not store it"); render(); return; }
    if (f.size > PDF_MAX) { toast("That PDF is over 4 MB - paste the text instead"); return; }
    const r = new FileReader();
    r.onload = () => {
      const b64 = String(r.result).split(",")[1] || "";
      if (put(K.pdf, b64) && put(K.pdfName, f.name)) toast("Saved");
      else toast("This browser would not store the PDF - paste the text instead");
      render();
    };
    r.onerror = () => toast("Could not read that file");
    r.readAsDataURL(f);
  }

  // The prompt for a chat window: the same brief, with the resume attached by
  // hand. Works with no key at all.
  function promptText() {
    const j = data.jobs[current];
    const jd = ($("orJd") && $("orJd").value.trim()) || "";
    return brief(j, jd, !!get(K.pdf) || !get(K.text).trim()) +
      "\n\nReturn the result as readable sections rather than JSON.";
  }

  function open(id) {
    if (!data || !data.jobs[id]) { toast("Still loading the tracker..."); return; }
    if (inflight && current !== id) inflight.abort("switched");
    current = id;
    render();
    $("ordlg").showModal();
  }

  $("ordlg").addEventListener("click", e => {
    if (e.target === $("ordlg")) { $("ordlg").close(); return; }
    const b = e.target.closest("[data-or]");
    if (!b) return;
    const act = b.dataset.or;
    if (act === "close") $("ordlg").close();
    else if (act === "draft") runDraft();
    else if (act === "setup") { $("orSetup").open = true; $("orKey").focus(); }
    else if (act === "save") saveSetup();
    else if (act === "droppdf") { put(K.pdf, ""); put(K.pdfName, ""); render(); }
    else if (act === "forget") {
      [K.key, K.pdf, K.pdfName, K.text].forEach(k => put(k, ""));
      toast("Key and resume removed from this browser");
      render();
    }
    else if (act === "copy") copy($(b.dataset.from).value, b.dataset.from === "orNote" ? "Note" : "Email");
    else if (act === "prompt") copy(promptText(), "Prompt");
    else if (act === "mail") {
      location.href = "mailto:?subject=" + encodeURIComponent($("orSubject").value) +
        "&body=" + encodeURIComponent($("orEmail").value);
    }
  });
  $("ordlg").addEventListener("input", e => { if (e.target.id === "orNote") count(); });
  // closing mid-draft keeps the request running: its result is cached and
  // shows the next time this posting is opened

  window.openOutreach = open;
  // exposed for the test harness; nothing on the page calls these
  window.__outreach = { contacts, brief, request, parse, MODELS };
})();
