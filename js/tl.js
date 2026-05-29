window.addEventListener('load', function () {
    current_pager_page = current_page;

    if (inf_scroll && (tab == "media" || tab == "posts")) {
        window.addEventListener('scroll', () => {
            if ((window.innerHeight + window.scrollY) >= document.body.offsetHeight - 300) {
                append_page();
            }
        });
    }

    setTimeout(() => {
        // set all .card_media opacity to 1
        document.querySelectorAll(".card_media").forEach((e, n) => {
            e.style.opacity = 1;
        })
    }, 1500)

    document.querySelectorAll('.post_group').forEach(el => {
        offscreen_observer.observe(el);
        height_observer.observe(el);
    });

    if (parent && parent.show_navigation) {
        parent.show_navigation();
    }


    // swipe left and right to go to next/prev page on mobile

    container_outer.addEventListener('touchstart', function (e) {
        touch_start_x = e.changedTouches[0].screenX;
        touch_start_y = e.changedTouches[0].screenY;
    }, false);

    container_outer.addEventListener('touchend', function (e) {
        touch_end_x = e.changedTouches[0].screenX;
        touch_end_y = e.changedTouches[0].screenY;
        handle_gesture();
    }, false);

    threshold = 60;
});