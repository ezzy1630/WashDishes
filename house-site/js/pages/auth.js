import { brand, button, empty, field, fill, h, heading, icon } from "../ui.js";
import { getNames, redeem } from "../api.js";
import { configured, mock, signIn } from "../auth.js";
/** @param {import("../types.js").ScreenContext} context */
export function createAuthScreens(context) {
  const { root, state, href, navigate, enter } = context;
  async function loadNames() {
    try {
      state.names = (await getNames()).sort((a, b) =>
        a.name.localeCompare(b.name),
      );
      return true;
    } catch {
      return false;
    }
  }
  function renderAuth() {
    if (location.pathname.replace(/\/$/, "") === "/join") {
      renderJoin();
      return;
    }
    const select = h(
      "select",
      { name: "resident", required: true },
      h(
        "option",
        { value: "" },
        state.names.length ? "Pick your name" : "Loading names…",
      ),
      state.names.map((n) => h("option", { value: n.slug }, n.name)),
    );
    const password = h("input", {
      type: "password",
      name: "password",
      autocomplete: "current-password",
      required: true,
    });
    const error = h("p", { class: "form-error", role: "alert" });
    const submit = h(
      "button",
      { type: "submit", class: "primary" },
      "Sign in",
      icon("arrow"),
    );
    const form = h(
      "form",
      {
        onsubmit: async (/** @type {SubmitEvent} */ event) => {
          event.preventDefault();
          if (submit.disabled) return;
          submit.disabled = true;
          submit.textContent = "Signing in…";
          error.textContent = "";
          try {
            await signIn(select.value, password.value);
            password.value = "";
            await enter();
          } catch (e) {
            error.textContent =
              e instanceof Error ? e.message : "Couldn’t sign in. Try again.";
            password.focus();
          } finally {
            submit.disabled = false;
            submit.replaceChildren("Sign in", icon("arrow"));
          }
        },
      },
      field("Your name", select),
      field("Password", password),
      error,
      submit,
    );
    const connect = h("div", { class: "auth-connection" });
    fill(
      root,
      h("header", { class: "auth-header" }, brand(href, navigate)),
      mock
        ? h(
            "p",
            { class: "demo-banner" },
            "Local demo · Use password “house-demo”",
          )
        : null,
      h(
        "main",
        { id: "main", class: "auth" },
        h("div", { class: "auth-symbol", "aria-hidden": "true" }, icon("dish")),
        heading(
          "Good house. Shared kitchen.",
          "Sign in to see how this week is stacking up.",
        ),
        form,
        connect,
        h(
          "p",
          { class: "auth-help" },
          "First time here, or forgot your password?",
          h("br"),
          "Ask Ezzy for a fresh house link.",
        ),
      ),
    );
    if (!configured && !mock) {
      connect.append(
        h(
          "p",
          { class: "help" },
          "Sign-in is waiting for the house connection to be set up.",
        ),
      );
      submit.disabled = true;
    }
    if (!state.names.length) {
      select.disabled = true;
      submit.disabled = true;
      void loadNames().then((ok) => {
        if (!select.isConnected) return;
        fill(
          select,
          h("option", { value: "" }, "Pick your name"),
          state.names.map((n) => h("option", { value: n.slug }, n.name)),
        );
        select.disabled = !ok;
        submit.disabled = !ok || (!configured && !mock);
        if (!ok)
          connect.replaceChildren(
            h("p", { role: "status" }, "Dish Watch is offline right now"),
            button("Try again", renderAuth, "secondary"),
          );
      });
    }
    document.title = "Sign in · Highest House";
  }
  function renderJoin() {
    const token = location.hash.slice(1);
    const error = h("p", { class: "form-error", role: "alert" });
    const password = h("input", {
      type: "password",
      name: "password",
      autocomplete: "new-password",
      minlength: 8,
      required: true,
    });
    const confirm = h("input", {
      type: "password",
      name: "confirm",
      autocomplete: "new-password",
      minlength: 8,
      required: true,
    });
    const submit = h(
      "button",
      { class: "primary", type: "submit" },
      "Set password & come in",
      icon("arrow"),
    );
    const form = h(
      "form",
      {
        onsubmit: async (/** @type {SubmitEvent} */ event) => {
          event.preventDefault();
          if (submit.disabled) return;
          error.textContent = "";
          if (password.value.length < 8) {
            error.textContent = "Use at least 8 characters.";
            password.focus();
            return;
          }
          if (password.value !== confirm.value) {
            error.textContent = "Those passwords don’t match yet.";
            confirm.focus();
            return;
          }
          submit.disabled = true;
          submit.textContent = "Setting your password…";
          let saved = false;
          try {
            const { login } = await redeem(token, password.value);
            saved = true;
            history.replaceState({}, "", href("/join"));
            await signIn(login, password.value);
            password.value = "";
            confirm.value = "";
            history.replaceState({}, "", href("/"));
            await enter();
          } catch (e) {
            if (saved) {
              form.replaceChildren(
                h(
                  "p",
                  {},
                  "Your password is saved. Sign in with your name and new password.",
                ),
                h("a", { class: "primary", href: href("/") }, "Go to sign in"),
              );
            } else {
              error.textContent =
                e instanceof Error
                  ? e.message
                  : "Couldn’t use that link. Try again.";
              submit.disabled = false;
              submit.replaceChildren("Set password & come in", icon("arrow"));
            }
          }
        },
      },
      field(
        "New password",
        password,
        "At least 8 characters. Make it one you don’t use elsewhere.",
      ),
      field("Confirm password", confirm),
      error,
      submit,
    );
    fill(
      root,
      h("header", { class: "auth-header" }, brand(href, navigate)),
      mock
        ? h(
            "p",
            { class: "demo-banner" },
            "Local demo · Invite and password reset",
          )
        : null,
      h(
        "main",
        { id: "main", class: "auth" },
        heading(
          "Your place in the house.",
          "Set a password, or choose a new one. Same house, fresh start.",
        ),
        token
          ? form
          : empty(
              "This link needs a token",
              "Open the complete house link Ezzy sent you, or ask for a fresh one.",
            ),
        h(
          "a",
          { class: "text-link back-link", href: href("/") },
          "Back to sign in",
        ),
      ),
    );
    document.title = "Join the house · Dish Watch";
  }

  return { renderAuth, renderJoin };
}
