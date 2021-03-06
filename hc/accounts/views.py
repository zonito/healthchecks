import uuid

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import User
from django.core import signing
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from hc.accounts.forms import (EmailPasswordForm, ReportSettingsForm,
                               SetPasswordForm)
from hc.accounts.models import Profile
from hc.api.models import Channel, Check


def _make_user(email):
    username = str(uuid.uuid4())[:30]
    user = User(username=username, email=email)
    user.set_unusable_password()
    user.save()

    channel = Channel()
    channel.user = user
    channel.kind = "email"
    channel.value = email
    channel.email_verified = True
    channel.save()

    return user


def _associate_demo_check(request, user):
    if "welcome_code" in request.session:
        check = Check.objects.get(code=request.session["welcome_code"])

        # Only associate demo check if it doesn't have an owner already.
        if check.user is None:
            check.user = user
            check.save()

            check.assign_all_channels()

            del request.session["welcome_code"]


def login(request):
    bad_credentials = False
    if request.method == 'POST':
        form = EmailPasswordForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            password = form.cleaned_data["password"]
            if len(password):
                user = authenticate(username=email, password=password)
                if user is not None and user.is_active:
                    auth_login(request, user)
                    return redirect("hc-checks")
                bad_credentials = True
            else:
                try:
                    user = User.objects.get(email=email)
                except User.DoesNotExist:
                    user = _make_user(email)
                    _associate_demo_check(request, user)

                profile = Profile.objects.for_user(user)
                profile.send_instant_login_link()
                return redirect("hc-login-link-sent")

    else:
        form = EmailPasswordForm()

    bad_link = request.session.pop("bad_link", None)
    ctx = {
        "form": form,
        "bad_credentials": bad_credentials,
        "bad_link": bad_link
    }
    return render(request, "accounts/login.html", ctx)


def logout(request):
    auth_logout(request)
    return redirect("hc-index")


def login_link_sent(request):
    return render(request, "accounts/login_link_sent.html")


def set_password_link_sent(request):
    return render(request, "accounts/set_password_link_sent.html")


def check_token(request, username, token):
    if request.user.is_authenticated() and request.user.username == username:
        # User is already logged in
        return redirect("hc-checks")

    user = authenticate(username=username, token=token)
    if user is not None and user.is_active:
        # This should get rid of "welcome_code" in session
        request.session.flush()

        profile = Profile.objects.for_user(user)
        profile.token = ""
        profile.save()
        auth_login(request, user)

        return redirect("hc-checks")

    request.session["bad_link"] = True
    return redirect("hc-login")


@login_required
def profile(request):
    profile = Profile.objects.for_user(request.user)

    if request.method == "POST":
        if "set_password" in request.POST:
            profile.send_set_password_link()
            return redirect("hc-set-password-link-sent")

        form = ReportSettingsForm(request.POST)
        if form.is_valid():
            profile.reports_allowed = form.cleaned_data["reports_allowed"]
            profile.save()
            messages.info(request, "Your settings have been updated!")

    ctx = {
        "profile": profile
    }

    return render(request, "accounts/profile.html", ctx)


@login_required
def set_password(request, token):
    profile = Profile.objects.for_user(request.user)
    if not check_password(token, profile.token):
        return HttpResponseBadRequest()

    if request.method == "POST":
        form = SetPasswordForm(request.POST)
        if form.is_valid():
            password = form.cleaned_data["password"]
            request.user.set_password(password)
            request.user.save()

            profile.token = ""
            profile.save()

            # Setting a password logs the user out, so here we
            # log them back in.
            u = authenticate(username=request.user.email, password=password)
            auth_login(request, u)

            messages.info(request, "Your password has been set!")
            return redirect("hc-profile")

    ctx = {
    }

    return render(request, "accounts/set_password.html", ctx)


def unsubscribe_reports(request, username):
    try:
        signing.Signer().unsign(request.GET.get("token"))
    except signing.BadSignature:
        return HttpResponseBadRequest()

    user = User.objects.get(username=username)
    profile = Profile.objects.for_user(user)
    profile.reports_allowed = False
    profile.save()

    return render(request, "accounts/unsubscribed.html")
