import json
import time
import urllib.parse as urlparse
from enum import Enum
from urllib.parse import parse_qs

import colored
from colored import stylize
from selenium.common import exceptions

from src import twitter, browser


class EntryStates(Enum):
    DEFAULT = 0
    EXPANDED = 1
    COMPLETED = 2
    HIDDEN = 3


def make_whitelist(entry_types, user_info):
    whitelist = []

    auths = user_info['contestant']['authentications']

    for auth in auths:
        if auth['provider'] in entry_types:
            whitelist.extend(entry_types[auth['provider']])

    whitelist.extend(entry_types['other'])
    whitelist.extend(entry_types['visit_view'])

    return whitelist


def get_info():
    not_found_elem = browser.get_elem_by_css("img[src='/images/error/404.png']")
    if not_found_elem:
        print("\tPage doesn't exist", end='')
        return None, None

    contestant = browser.wait_until_found("div[ng-controller='EnterController']", 7)
    campaign = browser.wait_until_found("div[ng-controller='EnterController']>div[ng-init^='initCampaign']", 1)

    # if the info was not found it is probably in an iframe
    if campaign is None:
        iframe = browser.wait_until_found("iframe[id^='GleamEmbed']", 7)
        if iframe is None:
            return None, None

        try:
            browser.driver.switch_to.frame(iframe)

            contestant = browser.wait_until_found("div[ng-controller='EnterController']", 7)
            campaign = browser.wait_until_found("div[ng-controller='EnterController']>div[ng-init^='initCampaign']", 1)

            if campaign is None:
                browser.driver.switch_to.default_content()
                return None, None

        except exceptions.NoSuchFrameException:
            return None, None

    campaign_info_str = campaign.get_attribute("ng-init")
    campaign_info_str = campaign_info_str.replace("initCampaign(", "")[:-1]

    campaign_info_json = json.loads(campaign_info_str)

    contestant_info_str = contestant.get_attribute("ng-init")

    entry_count = contestant_info_str[contestant_info_str.find("initEntryCount(") + 15:contestant_info_str.rfind(")")]
    entry_count = int(entry_count) if entry_count != "" else -1

    contestant_info_str = contestant_info_str[contestant_info_str.find("{"):contestant_info_str.rfind("}") + 1]

    contestant_info_json = json.loads(contestant_info_str)

    # add the number of total entries to the dict
    campaign_info_json['total_entries'] = entry_count

    if not contestant_info_json['location_allowed']:
        print("\tNot available in your country", end='')
        return None, None

    return campaign_info_json, contestant_info_json


def create_entry_method_strings(entry_method):
    entry_method_str = f"entry method: {entry_method['id']} ({entry_method['entry_type']})"
    strings = {
        "default_str": entry_method_str,
        "success_str": '\r' + stylize("\tDid " + entry_method_str + "                        ", colored.fg("green")),
        "fail_str": '\r' + stylize("\tDid " + entry_method_str + "                        ", colored.fg("red")),
        "ignored_str": '\r' + stylize("\tIgnored " + entry_method_str + "                        ", colored.fg("grey_46")),
        "couldnt_see_str": '\r' + stylize("\tCouldn't see " + entry_method_str + "                        ", colored.fg("grey_46")),
        "will_revisit_str": '\r' + stylize("\tWill revisit " + entry_method_str + "                        ", colored.fg("yellow"))
    }

    return strings


def do_giveaway(giveaway_info, whitelist):
    main_window = browser.driver.current_window_handle
    elems_to_revisit = []
    campaign = giveaway_info['campaign']
    entry_methods = giveaway_info['entry_methods']

    browser.storage.clear()

    # put the mandatory entry methods first
    entry_methods_not_mandatory = [entry_method for entry_method in entry_methods if not entry_method['mandatory']]
    entry_methods = [entry_method for entry_method in entry_methods if entry_method['mandatory']]

    entry_methods.extend(entry_methods_not_mandatory)

    if campaign['finished'] or campaign['paused']:
        print("\n\tGiveaway has ended")
        return

    for entry_method in entry_methods:
        entry_method_strings = create_entry_method_strings(entry_method)

        print(f"\n\tDoing {entry_method_strings['default_str']})", end='')
        try:
            minimize_all_entries()
        except:
            return

        if entry_method['entry_type'] not in whitelist:
            print(entry_method_strings['ignored_str'], end='')
            continue

        entry_method_elem, state = get_entry_elem(entry_method['id'])
        if entry_method_elem is None:
            continue

        if state == EntryStates.DEFAULT:
            try:
                entry_method_elem.click()
            except exceptions.ElementClickInterceptedException:
                continue

        elif state == EntryStates.COMPLETED:
            if state == EntryStates.COMPLETED:
                print(entry_method_strings['success_str'], end='')
            else:
                print(entry_method_strings['fail_str'], end='')
            continue

        elif state == EntryStates.HIDDEN:
            print(entry_method_strings['couldnt_see_str'], end='')
            continue

        wait_until_entry_loaded(entry_method['id'])

        entry_method_elem, state = get_entry_elem(entry_method['id'])
        if entry_method_elem is None:
            continue

        if state == EntryStates.COMPLETED:
            if state == EntryStates.COMPLETED:
                print(entry_method_strings['success_str'], end='')
            else:
                print(entry_method_strings['fail_str'], end='')
            continue

        to_revisit = do_entry(entry_method_elem, entry_method['entry_type'], entry_method['id'])

        if to_revisit:
            elems_to_revisit.append(entry_method)

        cont_btn = get_continue_elem(entry_method_elem)
        if cont_btn is None:
            continue

        try:
            cont_btn.click()
        except:
            pass

        wait_until_entry_loaded(entry_method['id'])

        entry_method_elem, state = get_entry_elem(entry_method['id'])
        if entry_method_elem is None:
            continue

        if state == EntryStates.COMPLETED:
            print(entry_method_strings['success_str'], end='')
        elif to_revisit:
            print(entry_method_strings['will_revisit_str'], end='')
        else:
            print(entry_method_strings['fail_str'], end='')

        browser.driver.switch_to.window(main_window)
        time.sleep(0.2)

    if len(elems_to_revisit) == 0:
        return

    print("\n\n\tRevisiting some entry methods:", end='')
    browser.refresh()
    for entry_method in elems_to_revisit:
        entry_method_strings = create_entry_method_strings(entry_method)

        print(f"\n\tDoing {entry_method_strings['default_str']})", end='')

        try:
            minimize_all_entries()
        except:
            return

        entry_method_elem, state = get_entry_elem(entry_method['id'])
        if entry_method_elem is None:
            continue

        if state == EntryStates.DEFAULT:
            try:
                entry_method_elem.click()
            except exceptions.ElementClickInterceptedException:
                continue
        elif state is EntryStates.COMPLETED:
            print(entry_method_strings['success_str'], end='')
        else:
            continue

        wait_until_entry_loaded(entry_method['id'])

        cont_btn = get_continue_elem(entry_method_elem)
        if cont_btn is None:
            continue

        try:
            cont_btn.click()
        except:
            pass

        wait_until_entry_loaded(entry_method['id'])

        entry_method_elem, state = get_entry_elem(entry_method['id'])
        if entry_method_elem is None:
            continue

        if state == EntryStates.COMPLETED:
            print(entry_method_strings['success_str'], end='')
        else:
            print(entry_method_strings['fail_str'], end='')

        time.sleep(0.5)


def do_entry(entry_method_elem, entry_type, entry_id):
    if entry_type == 'twitter_follow':
        try:
            tweet_btn = entry_method_elem.find_element_by_css_selector("div[class='expandable']>div>div>div>div>div>a")
        except exceptions.NoSuchElementException:
            return

        follow_url = tweet_btn.get_attribute("href")
        name = follow_url[follow_url.find("=") + 1:]

        twitter.follow(name)

    elif entry_type == 'twitter_retweet':
        try:
            retweet_elem = entry_method_elem.find_element_by_css_selector(
                "div[class='expandable']>div>div>div>div>div>twitter-widget")
        except exceptions.NoSuchElementException:
            return

        tweet_id = retweet_elem.get_attribute("data-tweet-id")

        twitter.retweet(tweet_id)

    elif entry_type == 'twitter_tweet':
        try:
            tweet_elem = entry_method_elem.find_element_by_css_selector(
                "div[class='expandable']>div>div>div>div>div>a[class*='twitter']")
        except exceptions.NoSuchElementException:
            return

        tweet_url = tweet_elem.get_attribute("href")

        parsed = urlparse.urlparse(tweet_url)
        text = parse_qs(parsed.query)['text']
        if len(text) == 0:
            return
        text = text[0]

        twitter.tweet(text)

    elif entry_type == 'twitter_hashtags':
        try:
            expandable_elem = entry_method_elem.find_element_by_css_selector("div[class='expandable']")
            tweet_elem = expandable_elem.find_element_by_css_selector("a[class*='twitter']")
        except exceptions.NoSuchElementException:
            return

        tweet_url = tweet_elem.get_attribute("href")

        parsed = urlparse.urlparse(tweet_url)
        parsed = parse_qs(parsed.query)
        if 'hashtags' not in parsed:
            return

        hashtags = parsed['hashtags']
        if len(hashtags) == 0:
            return
        hashtags = hashtags[0].split(',')

        to_tweet = ""
        for hashtag in hashtags:
            to_tweet += f"#{hashtag} "

        twitter.tweet(to_tweet)

        try:
            already_tweeted_elem = expandable_elem.find_element_by_css_selector(
                "div>div>div>div>a[ng-click^='saveEntry']")

            already_tweeted_elem.click()
        except:
            pass

    elif entry_type.count("visit") > 0 or entry_type == 'custom_action':
        millis = int(round(time.time() * 1000))

        # set a storage entry to fake a visit
        browser.storage[f"D-{entry_id}"] = f"{{\"c\":{millis},\"o\":{{\"expires\":7}},\"v\":\"V\"}}"

        # if there is a minimum time on the entry set another storage entry
        try:
            timer_elem = entry_method_elem.find_element_by_css_selector("span[ng-hide^='!(isTimerAction']")

            if timer_elem.text.count("NaN") == 0 and timer_elem.text != "":
                browser.storage[f"T-{entry_id}"] = f"{{\"c\":{millis},\"o\":{{\"expires\":1}},\"v\":{int(time.time() - 300)}}}"

                return True
        except exceptions.NoSuchElementException:
            pass

    elif entry_type == 'loyalty':
        try:
            expandable_elem = entry_method_elem.find_element_by_css_selector("div[class='expandable']")
            claim_elem = expandable_elem.find_element_by_css_selector("span[class='tally']")
        except exceptions.NoSuchElementException:
            return

        try:
            claim_elem.click()
        except exceptions.ElementNotInteractableException:
            return

    elif entry_type == 'instagram_view_post' or entry_type == 'twitter_view_post' or entry_type == 'facebook_view_post':
        time.sleep(6)


def get_entry_elem(entry_id):
    entry_method_elem = browser.get_elem_by_css(f"div[class^='entry-method'][id='em{entry_id}']")
    if not entry_method_elem:
        return None, None

    state = entry_method_elem.get_attribute('class')

    if entry_method_elem.size['height'] == 0:
        state = EntryStates.HIDDEN

    elif state.count('expanded'):
        state = EntryStates.EXPANDED

    elif state.count('complete'):
        state = EntryStates.COMPLETED

    else:
        state = EntryStates.DEFAULT

    return entry_method_elem, state


def wait_until_entry_loaded(entry_id):
    browser.wait_until_found(f"div.entry-method[id='em{entry_id}']>a:not(.loading)", 4)


def get_continue_elem(parent_elem):
    # continue button
    try:
        cont_btn = parent_elem.find_element_by_css_selector("div[class^='form-actions']>div>a")
    except exceptions.NoSuchElementException:
        try:
            cont_btn = parent_elem.find_element_by_css_selector("div[class^='form-actions']>button")
        except exceptions.NoSuchElementException:
            try:
                cont_btn = parent_elem.find_element_by_css_selector("div[class^='form-actions']>div")
            except exceptions.NoSuchElementException:
                try:
                    cont_btn = parent_elem.find_element_by_css_selector(
                        "div[class^='form-actions']>a[ng-click^='saveEntry']")
                except exceptions.NoSuchElementException:
                    return None

    return cont_btn


def minimize_all_entries():
    entry_method_elems = browser.get_elems_by_css("div[class^='entry-method'][class*='expanded']")
    for entry_method_elem in entry_method_elems:
        entry_method_elem.click()