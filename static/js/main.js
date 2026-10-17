/* DhanSutra auth pages: login, signup, forgot password.
   Plain JavaScript (no jQuery needed). Server-side checks remain the real validation;
   this only gives quick feedback before the form is sent. */
(function () {
    "use strict";

    var MIN_PASSWORD_LENGTH = 8; // keep in step with MIN_PASSWORD_LENGTH in main.py
    var EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    // Follow the dark/light choice made on the dashboard
    try {
        if (localStorage.getItem("dhanSutraDark") === "true") {
            document.documentElement.classList.add("dark");
        }
    } catch (e) { /* storage blocked: stay on the light theme */ }

    function setInvalid(input, message) {
        var field = input.closest(".field");
        if (!field) return;
        field.classList.add("invalid");
        var box = field.querySelector(".field-error");
        if (box) box.textContent = message;
    }

    function clearInvalid(input) {
        var field = input.closest(".field");
        if (field) field.classList.remove("invalid");
    }

    // Show / hide password (buttons carry data-target="<input id>")
    document.querySelectorAll(".toggle-password").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var input = document.getElementById(btn.getAttribute("data-target"));
            if (!input) return;
            var show = input.type === "password";
            input.type = show ? "text" : "password";
            btn.textContent = show ? "🙈" : "👁️";
            btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
        });
    });

    var form = document.getElementById("validate-form");
    if (!form) return; // pages without a form (e.g. forgot password) need nothing more

    form.querySelectorAll("input").forEach(function (input) {
        input.addEventListener("input", function () { clearInvalid(input); });
    });

    form.addEventListener("submit", function (e) {
        var mode = form.getAttribute("data-mode"); // "login" or "signup"
        var username = document.getElementById("username");
        var password = document.getElementById("password");
        var ok = true;

        var name = username.value.trim();
        if (!name) {
            setInvalid(username, mode === "signup" ? "Email is required." : "Email or username is required.");
            ok = false;
        } else if (mode === "signup" && !EMAIL_PATTERN.test(name)) {
            setInvalid(username, "Please enter a valid email address.");
            ok = false;
        }

        if (!password.value) {
            setInvalid(password, "Password is required.");
            ok = false;
        } else if (mode === "signup" && password.value.length < MIN_PASSWORD_LENGTH) {
            setInvalid(password, "Password must be at least " + MIN_PASSWORD_LENGTH + " characters.");
            ok = false;
        }

        if (!ok) {
            e.preventDefault();
            return;
        }
        username.value = name; // send the trimmed value
        var button = form.querySelector("button[type=submit]");
        if (button) {
            button.disabled = true; // stop double submits (the login rate limit counts every try)
            button.textContent = mode === "signup" ? "Creating account..." : "Signing in...";
        }
    });
})();
