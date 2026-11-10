import { empty, field, fill, h, heading, icon } from "../ui.js";
import { HouseError, redeem } from "../api.js";
import {
  configured,
  mock,
  signIn,
  rememberedName,
  loginSlug,
} from "../auth.js";
/** @param {import("../types.js").ScreenContext} context */
export function createAuthScreens(context) {
  const { root, href, enter } = context;
  function renderAuth() {
    if (location.pathname.replace(/\/$/, "") === "/join") {
      renderJoin();
      return;
    }
    const savedName = rememberedName();
    const name = h("input", {
      type: "text",
      name: "resident",
      autocomplete: "username",
      required: true,
      value: savedName,
      autocapitalize: "words",
      spellcheck: "false",
    });
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
            await signIn(
              name.value === savedName ? savedName : loginSlug(name.value),
              password.value,
            );
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
      field("Your name", name),
      field("Password", password),
      error,
      submit,
    );
    const connect = h("div", { class: "auth-connection" });
    fill(
      root,
      mock
        ? h(
            "p",
            { class: "demo-banner" },
            "Demo · The password is house-demo",
          )
        : null,
      h(
        "main",
        { id: "main", class: "auth" },
        h("img", { class: "auth-mark", src: "/mark.svg", alt: "", width: 64, height: 64 }),
        heading("Highest House", "Sign in to see this week’s kitchen."),
        form,
        connect,
        h(
          "p",
          { class: "auth-help" },
          "First time, or forgot your password? Ask Ezzy for a new link.",
        ),
      ),
    );
    if (!configured && !mock) {
      connect.append(
        h(
          "p",
          { class: "help" },
          "Sign-in isn’t set up yet. Ask Ezzy.",
        ),
      );
      submit.disabled = true;
    }
    if (name.value) password.focus();
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
              if (e instanceof HouseError && [400, 410].includes(e.status)) {
                submit.disabled = true;
                submit.textContent = "Ask Ezzy for a new link";
                password.value = "";
                confirm.value = "";
              }
            }
          }
        },
      },
      field(
        "New password",
        password,
        "At least 8 characters.",
      ),
      field("Confirm password", confirm),
      error,
      submit,
    );
    fill(
      root,
      mock
        ? h(
            "p",
            { class: "demo-banner" },
            "Demo · Invite and password reset",
          )
        : null,
      h(
        "main",
        { id: "main", class: "auth" },
        heading(
          "Choose a password",
          "You’ll use it with your name to sign in.",
        ),
        token
          ? form
          : empty(
              "This link is incomplete",
              "Open the whole link Ezzy sent you, or ask for a new one.",
            ),
        h(
          "a",
          { class: "text-link back-link", href: href("/") },
          "Back to sign in",
        ),
      ),
    );
    document.title = "Choose a password · Highest House";
  }

  return { renderAuth, renderJoin };
}
