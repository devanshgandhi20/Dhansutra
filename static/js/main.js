
(function ($) {
    "use strict";


    /*==================================================================
    [ Validate ]*/
    var input = $('.validate-input .input100');

    $('.validate-forssm').on('submit', function () {
        var check = true;

        for (var i = 0; i < input.length; i++) {
            if (validate(input[i]) == false) {
                showValidate(input[i]);
                check = false;
            }
        }

        return check;
    });


    $('.validate-form .input100').each(function () {
        $(this).focus(function () {
            hideValidate(this);
        });
    });

    function validate(input) {
        if ($(input).attr('type') == 'email' || $(input).attr('name') == 'username') {
            if ($(input).val().trim().match(/^([a-zA-Z0-9_\-\.]+)@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.)|(([a-zA-Z0-9\-]+\.)+))([a-zA-Z]{1,5}|[0-9]{1,3})(\]?)$/) == null) {
                return false;
            }
        }
        else {
            if ($(input).val().trim() == '') {
                return false;
            }
        }
    }

    function showValidate(input) {
        var thisAlert = $(input).parent();

        $(thisAlert).addClass('alert-validate');
    }

    function hideValidate(input) {
        var thisAlert = $(input).parent();

        $(thisAlert).removeClass('alert-validate');
    }



})(jQuery);

function togglePassword() {
    const password = document.getElementById("password");
    const eyeIcon = document.getElementById("eyeIcon");

    if (password.type === "password") {
        password.type = "text";
        eyeIcon.classList.remove("fa-eye");
        eyeIcon.classList.add("fa-eye-slash");
    } else {
        password.type = "password";
        eyeIcon.classList.remove("fa-eye-slash");
        eyeIcon.classList.add("fa-eye");
    }
}

document.getElementById('validate-form').addEventListener('submit', function (e) {
    e.preventDefault(); // prevent form from submitting immediately

    const emailInput = document.getElementById('username').value.trim();
    const passwordInput = document.getElementById('password').value.trim();

    const formType = this.getAttribute('action'); // determine page based on action URL
    const emailLabel = formType.includes('create_user') ? 'Email' : 'Username';

    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    if (!emailInput) {
        Swal.fire({
            icon: 'warning',
            title: 'Oops!',
            text: `${emailLabel} is required.`,
            confirmButtonColor: '#d33'
        });
        return;
    }

    if (!emailPattern.test(emailInput)) {
        Swal.fire({
            icon: 'warning',
            text: `Please enter a valid ${emailLabel.toLowerCase()}.`,
            confirmButtonColor: '#d33'
        });
        return;
    }

    if (!passwordInput) {
        Swal.fire({
            icon: 'warning',
            title: 'Oops!',
            text: 'Password is required.',
            confirmButtonColor: '#d33'
        });
        return;
    }

    // If all is valid, submit the form
    this.submit();
});