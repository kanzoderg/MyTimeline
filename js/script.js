window.addEventListener('load', function () {
    window.onhashchange = locationHashChanged;

    const inf_hint = document.getElementById("inf_hint");
    if (inf_scroll && (tab == "posts" || tab == "media")) {
        // inf_hint.style.display = "inline-block";
        document.getElementById("btn_next").style.display = "none";
        document.getElementById("btn_prev").style.display = "none";
    }

    var page_control = document.getElementById("page_control");

    if (max_page <= 1) {
        page_control.style.display = "none";
    }

    // capture left and right arrow keys to go to next/prev page
    document.addEventListener('keydown', function (e) {
        if (overlay && overlay.style.display === 'block') {
            if (e.key === 'Escape') {
                hide_overlay();
            }
            else if (e.key === 'ArrowRight') {
                full_next_btn = document.getElementById("full_card_next");
                if (full_next_btn) {
                    full_next_btn.click();
                }
            } else if (e.key === 'ArrowLeft') {
                full_prev_btn = document.getElementById("full_card_prev");
                if (full_prev_btn) {
                    full_prev_btn.click();
                }
            }
            return;
        }
        if (e.key === 'ArrowLeft') {
            prev_page();
        } else if (e.key === 'ArrowRight') {
            next_page();
        }
        else if (e.key === 'Escape') {
            if (overlay_on) {
                overlay_on = false;
                history.back();
            }
        }
    })

    if (current_q) {
        add_search_history_item(current_q);
        if (current_url.includes("?"))
            current_url = current_url + "&q=" + current_q;
        else {
            current_url = current_url + "?q=" + current_q;
        }
        if (search_bar_input)
            search_bar_input.value = decodeURIComponent(current_q);
        update_search_q_placeholders();
    }

    // find all .iconusername and add show_loading_icon on click
    const icon_usernames = document.querySelectorAll('.iconusername');
    icon_usernames.forEach(icon => {
        icon.addEventListener('click', show_loading_icon);
    });


    set_overflow_hidden(document);

    const user_body_text = document.querySelector('.user_body_text');
    if (user_body_text) {
        if (isOverflown(user_body_text)) {
            user_body_text.classList.add("fade-text");
            view_all_btn = document.createElement('a')
            view_all_btn.classList.add('view_all_btn')
            view_all_btn.setAttribute("onclick", "show_full_user_info()")
            view_all_btn.innerText = 'Expand'
            user_body_text.after(view_all_btn)
        }
    }

    hide_loading_icon();
});