import logging
import urllib
import commands
import dbutil
import paste.deploy.converters as converters
import genshi
import pylons
import ckan.lib.helpers as h
import ckan.plugins as p
import gasnippet

log = logging.getLogger('ckanext.googleanalytics')


class GoogleAnalyticsException(Exception):
    pass


class GoogleAnalyticsPlugin(p.SingletonPlugin):
    p.implements(p.IConfigurable, inherit=True)
    p.implements(p.IGenshiStreamFilter, inherit=True)
    p.implements(p.IRoutes, inherit=True)
    p.implements(p.IConfigurer, inherit=True)

    def configure(self, config):
        if (not 'googleanalytics.id' in config):
            msg = "Missing googleanalytics.id in config"
            raise GoogleAnalyticsException(msg)

        ga_id = config['googleanalytics.id']
        ga_domain = config.get('googleanalytics.domain', 'auto')
        js_url = h.url_for_static('/scripts/ckanext-googleanalytics.js')
        self.resource_url = config.get('googleanalytics.resource_prefix',
                                       commands.DEFAULT_RESOURCE_URL_TAG)
        self.show_downloads = converters.asbool(
            config.get('googleanalytics.show_downloads', True)
        )
        self.track_events = converters.asbool(
            config.get('googleanalytics.track_events', False)
        )

        self.header_code = genshi.HTML(
            gasnippet.header_code % (ga_id, ga_domain))
        self.footer_code = genshi.HTML(gasnippet.footer_code % js_url)

    def update_config(self, config):
        p.toolkit.add_template_directory(config, 'legacy_templates')
        p.toolkit.add_public_directory(config, 'legacy_public')

    def after_map(self, map):
        map.redirect("/analytics/package/top", "/analytics/dataset/top")
        map.connect(
            'analytics', '/analytics/dataset/top',
            controller='ckanext.googleanalytics.controller:GAController',
            action='view'
        )
        return map

    def filter(self, stream):
        log.info("Inserting Google Analytics code into template")

        stream = stream | genshi.filters.Transformer('head').append(
              self.header_code)

        if self.track_events:
            stream = stream | genshi.filters.Transformer(
                    'body/div[@id="scripts"]').append(self.footer_code)

        routes = pylons.request.environ.get('pylons.routes_dict')
        action = routes.get('action')
        controller = routes.get('controller')

        if (controller == 'package' and \
            action in ['search', 'read', 'resource_read']) or \
            (controller == 'group' and action == 'read'):

            log.info("Tracking of resource downloads")

            # add download tracking link
            def js_attr(name, event):
                attrs = event[1][1]
                href = attrs.get('href').encode('utf-8')
                link = '%s%s' % (self.resource_url, urllib.quote(href))
                js = "javascript: _gaq.push(['_trackPageview', '%s']);" % link
                return js

            # add some stats
            def download_adder(stream):
                download_html = '''<span class="downloads-count">
                [downloaded %s times]</span>'''
                count = None
                for mark, (kind, data, pos) in stream:
                    if mark and kind == genshi.core.START:
                        href = data[1].get('href')
                        if href:
                            count = dbutil.get_resource_visits_for_url(href)
                    if count and mark is genshi.filters.transform.EXIT:
                        # emit count
                        yield genshi.filters.transform.INSIDE, (
                            genshi.core.TEXT,
                            genshi.HTML(download_html % count), pos)
                    yield mark, (kind, data, pos)

            # perform the stream transform
            stream = stream | genshi.filters.Transformer(
                '//a[contains(@class, "resource-url-analytics")]').attr(
                    'onclick', js_attr)

            if (self.show_downloads and action == 'read' and
                controller == 'package'):
                stream = stream | genshi.filters.Transformer(
                    '//a[contains(@class, "resource-url-analytics")]').apply(
                        download_adder)
                stream = stream | genshi.filters.Transformer('//head').append(
                    genshi.HTML(gasnippet.download_style))

        return stream
