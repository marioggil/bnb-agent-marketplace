/* bnb_agent x402 browser signer (FU-2, spec W1-W4; design id 52 D7).
 *
 * Flow (Q6): click the Hire CTA → POST /api/hires (201 + B402 challenge) →
 * sign EIP-712 TransferWithAuthorization over $U with ethers v6
 * (`BrowserProvider.getSigner().signTypedData(...)`) → POST
 * /api/hires/{id}/pay with the base64 X-PAYMENT envelope → redirect to the
 * agent's `agent_url` when it is http(s), otherwise back to the detail page
 * (W3 — never a dead-end). On failure the status UI shows `failed` with the
 * server's error message and the page stays usable (W4).
 *
 * Validity window (D7): nonce = randomBytes(32), validAfter = now - 120
 * (Studio backdate rule), validBefore = now + maxTimeoutSeconds.
 *
 * The template exposes the wiring via data-* attributes on #hire-cta
 * (agent_id, agent_url, csrf) — the tests validate those attributes, not
 * this script's execution.
 */
(function () {
  "use strict";

  function el(id) {
    return document.getElementById(id);
  }

  function setStatus(state, message) {
    var box = el("hire-status");
    if (!box) return;
    box.dataset.state = state;
    // State is driven by `is-*` classes on the container (DESIGN.md: prefer
    // is-* classes over inline style.display); the CSS shows the matching
    // .status-* span.
    var states = ["idle", "pending", "paid", "failed"];
    for (var i = 0; i < states.length; i++) {
      box.classList.remove("is-" + states[i]);
    }
    box.classList.add("is-" + state);
    if (state === "failed" && message) {
      var err = el("hire-error");
      if (err) err.textContent = message;
    }
  }

  function randomNonceHex() {
    var bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes); // D7: nonce = randomBytes(32)
    var hex = "";
    for (var i = 0; i < bytes.length; i++) {
      hex += bytes[i].toString(16).padStart(2, "0");
    }
    return "0x" + hex;
  }

  function toBase64Envelope(envelope) {
    // ASCII-safe: the envelope carries only addresses/hex/digits.
    return btoa(JSON.stringify(envelope));
  }

  document.addEventListener("DOMContentLoaded", function () {
    var cta = el("hire-cta");
    if (!cta || cta.disabled) return; // no payTo → server disables the CTA

    cta.addEventListener("click", async function () {
      var csrf = cta.dataset.csrf || "";
      var agentUrl = cta.dataset.agentUrl || "";
      var headers = { "X-CSRF-Token": csrf };

      try {
        setStatus("pending");
        var create = await fetch("/api/hires", {
          method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, headers),
          body: JSON.stringify({ agent_id: cta.dataset.agentId }),
        });
        if (!create.ok) {
          throw new Error("hire create failed (HTTP " + create.status + ")");
        }
        var hire = await create.json();
        var challenge = hire.challenge;
        var accept = challenge.accepts[0];

        if (typeof window.ethereum === "undefined") {
          throw new Error("no wallet detected (window.ethereum missing)");
        }
        var provider = new ethers.BrowserProvider(window.ethereum);
        var signer = await provider.getSigner();

        var now = Math.floor(Date.now() / 1000); // seconds
        var authorization = {
          from: await signer.getAddress(),
          to: accept.payTo,
          value: BigInt(accept.amount),
          validAfter: now - 120, // D7 Studio backdate rule
          validBefore: now + accept.maxTimeoutSeconds,
          nonce: randomNonceHex(),
        };
        var domain = {
          name: accept.extra.name,
          version: accept.extra.version,
          chainId: parseInt(accept.network.split(":")[1], 10),
          verifyingContract: accept.asset,
        };
        var types = {
          TransferWithAuthorization: [
            { name: "from", type: "address" },
            { name: "to", type: "address" },
            { name: "value", type: "uint256" },
            { name: "validAfter", type: "uint256" },
            { name: "validBefore", type: "uint256" },
            { name: "nonce", type: "bytes32" }
          ]
        };
        var signature = await signer.signTypedData(domain, types, authorization);

        var envelope = {
          x402Version: challenge.x402Version,
          scheme: accept.scheme,
          network: accept.network,
          resource: challenge.resource,
          accepted: accept,
          payload: {
            signature: signature,
            authorization: {
              from: authorization.from,
              to: authorization.to,
              value: authorization.value.toString(),
              validAfter: authorization.validAfter.toString(),
              validBefore: authorization.validBefore.toString(),
              nonce: authorization.nonce
            }
          }
        };

        var pay = await fetch("/api/hires/" + hire.id + "/pay", {
          method: "POST",
          headers: Object.assign({ "X-PAYMENT": toBase64Envelope(envelope) }, headers),
        });
        if (!pay.ok) {
          var body = {};
          try {
            body = await pay.json();
          } catch (_ignored) { /* non-JSON error body */ }
          var message =
            (body.error && body.error.message) ||
            "payment failed (HTTP " + pay.status + ")";
          setStatus("failed", message);
          return; // stay on the page (W4)
        }

        setStatus("paid");
        // W3 — redirect to agent_url only when http(s), else the detail page.
        if (/^https?:\/\//i.test(agentUrl)) {
          window.location.href = agentUrl;
        } else {
          window.location.href = window.location.pathname;
        }
      } catch (err) {
        setStatus("failed", (err && err.message) ? err.message : String(err));
      }
    });
  });
})();
