/* ERC-8183 escrow hire handler (buyer-side, BSC mainnet).
 *
 * Flow:
 *   1. User clicks the secondary "Hire via Escrow" button on the agent page.
 *   2. We open the modal pre-filled with the agent's metadata.
 *   3. On confirm: POST /api/hires/escrow -> get 3 pre-armed txs.
 *   4. The browser signs and broadcasts them in order:
 *      a) approve($U, commerceProxy, budget)
 *      b) createJob(provider, router, expiredAt, task, router)
 *      c) fund(jobId, budget)   (we extract jobId from createJob's receipt logs)
 *   5. POST /api/hires/escrow/{hire_id}/submit with the on-chain jobId + tx hash.
 *   6. Poll getJob(jobId) every 5s to show Open -> Funded -> Submitted -> Completed.
 *
 * The user pays their own gas (MetaMask). The marketplace holds no funds.
 *
 * Loaded as a plain <script>; no build step. Relies on `window.ethers` and
 * `window.escrowConfig` (set in the data-* attributes of #hire-escrow-cta's
 * container).
 *
 * Spec: docs/category-study.md §ERC-8183 (buyer-side).
 */

(() => {
  "use strict";

  // ---------------------------------------------------------------------------
  // Tiny helpers
  // ---------------------------------------------------------------------------

  const $ = (sel, root = document) => root.querySelector(sel);

  const setStatus = (modal, state, message) => {
    const box = $("#hire-escrow-status", modal);
    if (!box) return;
    box.dataset.state = state;
    if (message) {
      const errSpan = $("#hire-escrow-error", modal);
      if (errSpan) errSpan.textContent = message;
    }
  };

  const setReadonly = (modal, readonly) => {
    $("#hire-escrow-task", modal).readOnly = readonly;
    $("#hire-escrow-budget", modal).readOnly = readonly;
    $("#hire-escrow-confirm", modal).disabled = readonly;
  };

  const fmtBudget = (weiBig) => {
    // 18 decimals. Show up to 6 fractional digits.
    const s = weiBig.toString().padStart(19, "0");
    const whole = s.slice(0, -18) || "0";
    const frac = s.slice(-18, -12); // first 6 decimals
    return `${whole}.${frac}`;
  };

  // ---------------------------------------------------------------------------
  // Modal open/close
  // ---------------------------------------------------------------------------

  const openModal = (wrap) => {
    const modal = $("#hire-escrow-modal");
    if (!modal) return;

    // Pre-fill agent data from the data-* attributes (cached server-side).
    const name = wrap.dataset.agentName || "agent";
    const desc = wrap.dataset.agentDescription || "No description provided.";
    const tags = JSON.parse(wrap.dataset.agentTags || "[]");
    const wallet = wrap.dataset.agentWallet || "";

    modal.querySelectorAll("[data-agent-name]").forEach((el) => (el.textContent = name));
    const descEl = modal.querySelector("[data-agent-desc]");
    if (descEl) descEl.textContent = desc;
    const walletEl = modal.querySelector("[data-agent-wallet]");
    if (walletEl) walletEl.textContent = wallet || "—";

    const tagsBox = modal.querySelector("[data-agent-tags]");
    if (tagsBox) {
      tagsBox.innerHTML = "";
      (tags || []).slice(0, 8).forEach((t) => {
        const span = document.createElement("span");
        span.className = "badge category";
        span.textContent = t;
        tagsBox.appendChild(span);
      });
    }

    // Reset form + status.
    const taskInput = $("#hire-escrow-task", modal);
    if (taskInput) taskInput.value = "";
    const budgetInput = $("#hire-escrow-budget", modal);
    if (budgetInput) budgetInput.value = (Number(wrap.dataset.defaultBudgetWei) / 1e18).toFixed(6);
    const sumEl = modal.querySelector("[data-budget-u]");
    if (sumEl) sumEl.textContent = budgetInput ? budgetInput.value : "0";

    setStatus(modal, "idle");
    setReadonly(modal, false);

    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
  };

  const closeModal = () => {
    const modal = $("#hire-escrow-modal");
    if (!modal) return;
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
  };

  // ---------------------------------------------------------------------------
  // Ethers helpers
  // ---------------------------------------------------------------------------

  const ensureEthers = () => {
    if (!window.ethers) {
      throw new Error("ethers not loaded (window.ethers missing)");
    }
  };

  const getBrowserSigner = async () => {
    if (!window.ethereum) {
      throw new Error("No wallet detected. Install MetaMask or another EVM wallet.");
    }
    const provider = new window.ethers.BrowserProvider(window.ethereum);
    await provider.send("eth_requestAccounts", []);
    return provider.getSigner();
  };

  /**
   * Send a pre-armed tx and wait for the receipt.
   * `tx` = {to, data, value, description} from the backend.
   */
  const sendArmedTx = async (signer, tx) => {
    const txReq = { to: tx.to, data: tx.data, value: tx.value || "0x0" };
    return await signer.sendTransaction(txReq);
  };

  /**
   * Extract jobId from the createJob tx receipt.
   * JobCreated event signature (per AgenticCommerceUpgradeable v1):
   *   JobCreated(uint256 indexed jobId, address indexed client,
   *              address indexed provider, address evaluator,
   *              uint256 expiredAt, address hook)
   */
  const extractJobIdFromCreateReceipt = (receipt, iface) => {
    // First, fast-path: ethers parseLog with the exact signature.
    for (const log of receipt.logs || []) {
      try {
        const parsed = iface.parseLog(log);
        if (parsed && parsed.name === "JobCreated") {
          return Number(parsed.args.jobId);
        }
      } catch (_) {
        // signature mismatch - try next log
      }
    }
    // Fallback: scan by topic0 = keccak of the canonical signature.
    // Guards against future ABI drift: even if our iface is stale,
    // we still find the jobId in topics[1] (first indexed param).
    if (window.ethers && window.ethers.id) {
      try {
        const topic0 = window.ethers.id(
          "JobCreated(uint256,address,address,address,uint256,address)"
        );
        for (const log of receipt.logs || []) {
          if ((log.topics || [])[0] === topic0) {
            const raw = log.topics[1];
            if (raw) return Number(BigInt(raw));
          }
        }
      } catch (_) {
        // ethers.id not available or hash failed
      }
    }
    return null;
  }

  // Minimal ABI surface for log parsing + getJob polling (subset of the
  // official AgenticCommerceUpgradeable ABI). Event signatures MUST match the
  // contract exactly (including indexed-ness and field order) or ethers'
  // parseLog will silently skip them — see bug fix escrow-event-signature.
  const COMMERCE_IFACE = new window.ethers.Interface([
    "event JobCreated(uint256 indexed jobId, address indexed client, address indexed provider, address evaluator, uint256 expiredAt, address hook)",
    "event JobFunded(uint256 indexed jobId, address indexed client, address indexed provider, uint256 amount)",
    "event JobSubmitted(uint256 indexed jobId, address indexed provider, bytes32 deliverable)",
    "function getJob(uint256 jobId) view returns (tuple(uint256 id, address client, address provider, address evaluator, string description, uint256 budget, uint256 expiredAt, uint8 status, address hook, uint256 submittedAt, bytes32 deliverable))",
  ]);

  const JOB_STATUS_NAMES = ["Open", "Funded", "Submitted", "Completed", "Rejected", "Expired"];

  // ---------------------------------------------------------------------------
  // Main flow
  // ---------------------------------------------------------------------------

  const startHire = async (wrap, modal) => {
    ensureEthers();

    // 1. Read form.
    const task = $("#hire-escrow-task", modal).value.trim();
    if (!task) {
      setStatus(modal, "failed", "Task is required.");
      return;
    }
    const budgetU = Number($("#hire-escrow-budget", modal).value);
    if (!Number.isFinite(budgetU) || budgetU <= 0) {
      setStatus(modal, "failed", "Budget must be a positive number.");
      return;
    }
    const budgetWei = BigInt(Math.round(budgetU * 1e18));

    setReadonly(modal, true);

    try {
      // 2. POST /api/hires/escrow to get pre-armed calls.
      setStatus(modal, "pending", "Creating job on the marketplace...");
      const csrf = wrap.dataset.csrf || "";
      const createResp = await fetch("/api/hires/escrow", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf,
        },
        body: JSON.stringify({
          agent_id: wrap.dataset.agentId,
          task,
          budget_wei: budgetWei.toString(),
        }),
      });
      if (!createResp.ok) {
        const err = await createResp.json().catch(() => ({}));
        throw new Error(
          err.error?.message || `create failed (HTTP ${createResp.status})`
        );
      }
      const data = await createResp.json();
      const hireId = data.hire_id;

      // 3. Sign the three txs.
      const signer = await getBrowserSigner();

      // a) approve
      setStatus(modal, "pending", "Sign: approve $U (popup 1/5)...");
      await sendArmedTx(signer, data.approve);

      // b) createJob
      setStatus(modal, "pending", "Sign: createJob (popup 2/5)...");
      const createReceipt = await sendArmedTx(signer, data.create_job);
      setStatus(modal, "mining", "Waiting for createJob to be mined...");
      const createMined = await createReceipt.wait(1);
      const jobId = extractJobIdFromCreateReceipt(createMined, COMMERCE_IFACE);
      if (!jobId) {
        throw new Error(
          `Could not find JobCreated event in the createJob receipt ` +
          `(tx=${createReceipt.hash}, logs=${(createMined.logs || []).length})`
        );
      }

      // c) registerJob — call the EVALUATOR ROUTER (not the commerce
      // kernel) to bind the OptimisticPolicy to this jobId. Without this,
      // fund() reverts with PolicyNotSet() inside the router's afterAction
      // callback. The policy address comes from the backend config.
      setStatus(
        modal,
        "pending",
        `Sign: registerJob on router (popup 3/5)...`
      );
      const routerIface = new window.ethers.Interface([
        "function registerJob(uint256 jobId, address policy)",
      ]);
      const policyAddress = data.policy_address;
      if (!policyAddress || policyAddress === "0x0000000000000000000000000000000000000000") {
        throw new Error(
          "OptimisticPolicy address not provided by the backend " +
          "(data.policy_address missing)"
        );
      }
      const registerJobData = routerIface.encodeFunctionData("registerJob", [
        jobId,
        policyAddress,
      ]);
      await sendArmedTx(signer, {
        to: data.router_address,
        data: registerJobData,
        value: "0x0",
      });

      // d) setBudget — createJob initializes the job with budget=0; we MUST
      // set the budget before fund() or it reverts with ZeroBudget().
      setStatus(
        modal,
        "pending",
        `Sign: setBudget for job #${jobId} (popup 4/5)...`
      );
      const commerceIface = new window.ethers.Interface([
        "function setBudget(uint256 jobId, uint256 amount, bytes optParams)",
        "function fund(uint256 jobId, uint256 expectedBudget, bytes optParams)",
      ]);
      const setBudgetData = commerceIface.encodeFunctionData("setBudget", [
        jobId,
        budgetWei,
        "0x",
      ]);
      await sendArmedTx(signer, {
        to: data.commerce_address,
        data: setBudgetData,
        value: "0x0",
      });

      // e) fund — now that policy + budget are set, fund() succeeds.
      setStatus(modal, "pending", `Sign: fund job #${jobId} (popup 5/5)...`);
      const fundData = commerceIface.encodeFunctionData("fund", [
        jobId,
        budgetWei,
        "0x",
      ]);
      const fundTx = { to: data.commerce_address, data: fundData, value: "0x0" };
      await sendArmedTx(signer, fundTx);

      // 4. Report the on-chain jobId + createJob tx hash back.
      setStatus(modal, "submitting", "Saving job id on the marketplace...");
      const submitResp = await fetch(`/api/hires/escrow/${hireId}/submit`, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf,
        },
        body: JSON.stringify({
          job_id: jobId,
          tx_hash: createReceipt.hash,
        }),
      });
      if (!submitResp.ok) {
        const err = await submitResp.json().catch(() => ({}));
        throw new Error(
          err.error?.message || `submit failed (HTTP ${submitResp.status})`
        );
      }

      // 5. Done. Start polling for on-chain status updates.
      setStatus(modal, "done", `Job #${jobId} funded. Polling for seller delivery...`);
      pollJobStatus(signer.provider, data.commerce_address, jobId, modal);
    } catch (err) {
      console.error("hire-escrow error:", err);
      setStatus(modal, "failed", err.message || String(err));
      setReadonly(modal, false);
    }
  };

  /**
   * Poll the contract for the job state and update the modal status line.
   * Stops after the job reaches a terminal state (Completed / Rejected / Expired).
   */
  const pollJobStatus = async (provider, commerceAddr, jobId, modal) => {
    const interval = 5000;
    const maxAttempts = 60; // 5 minutes
    for (let i = 0; i < maxAttempts; i++) {
      try {
        const job = await COMMERCE_IFACE.encodeFunctionData("getJob", [jobId]);
        const result = await provider.call({ to: commerceAddr, data: job });
        const decoded = COMMERCE_IFACE.decodeFunctionResult("getJob", result);
        const statusIdx = Number(decoded.status);
        const status = JOB_STATUS_NAMES[statusIdx] || `Unknown(${statusIdx})`;
        if (status === "Submitted") {
          setStatus(modal, "done", `Job #${jobId}: delivered by seller. You can settle now.`);
        } else if (status === "Completed") {
          setStatus(modal, "done", `Job #${jobId}: completed. Funds released.`);
          return;
        } else if (status === "Rejected" || status === "Expired") {
          setStatus(modal, "failed", `Job #${jobId}: ${status}. Claim a refund.`);
          return;
        } else {
          setStatus(modal, "done", `Job #${jobId}: ${status}. Waiting for seller...`);
        }
      } catch (err) {
        console.warn("poll failed:", err);
      }
      await new Promise((r) => setTimeout(r, interval));
    }
    setStatus(modal, "done", `Job #${jobId}: polling timed out. Refresh to check.`);
  };

  // ---------------------------------------------------------------------------
  // Bootstrap
  // ---------------------------------------------------------------------------

  const init = () => {
    const wrap = $(".hire-escrow-wrap");
    if (!wrap) return; // feature off for this page

    const openBtn = $("#hire-escrow-cta", wrap);
    if (openBtn) {
      openBtn.addEventListener("click", () => openModal(wrap));
    }
    const modal = $("#hire-escrow-modal");
    if (!modal) return;
    modal.querySelectorAll("[data-close]").forEach((el) =>
      el.addEventListener("click", closeModal)
    );
    const confirm = $("#hire-escrow-confirm", modal);
    if (confirm) {
      confirm.addEventListener("click", () => startHire(wrap, modal));
    }
    const budgetInput = $("#hire-escrow-budget", modal);
    if (budgetInput) {
      budgetInput.addEventListener("input", () => {
        const sumEl = modal.querySelector("[data-budget-u]");
        if (sumEl) sumEl.textContent = budgetInput.value;
      });
    }
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !modal.hidden) closeModal();
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
