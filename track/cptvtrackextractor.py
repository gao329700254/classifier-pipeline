from ml_tools.cptvfileprocessor import CPTVFileProcessor
from ml_tools.trackdatabase import TrackDatabase
from ml_tools import tools
from PIL import Image, ImageDraw

from track.trackextractor import TrackExtractor
from track.mpegpreviewstreamer import MPEGPreviewStreamer

import numpy as np
import matplotlib.pyplot as plt

import time
import os

class TrackerTestCase():
    def __init__(self):
        self.source = None
        self.tracks = []

def init_workers(lock):
    """ Initialise worker by setting the trackdatabase lock. """
    trackdatabase.HDF5_LOCK = lock


class CPTVTrackExtractor(CPTVFileProcessor):
    """
    Handles extracting tracks from CPTV files.
    Maintains a database recording which files have already been processed, and some statistics parameters used
    during processing.
    """

    # version number.  Recorded into stats file when a clip is processed.
    VERSION = 6

    def __init__(self, config):

        CPTVFileProcessor.__init__(self)

        self.config = config
        self.hints = {}
        self.verbose = False
        self.overwrite_mode = CPTVTrackExtractor.OM_NONE
        self.enable_previews = config.tracking.preview_tracks
        self.enable_track_output = True

        if config.tracking.preview_tracks:
            if os.path.exists(config.previews_colour_map):
                print("loading colour map " + config.previews_colour_map)
                self.colormap = tools.load_colormap(config.previews_colour_map)
            else:
                print("using default colour map")
                self.colormap = plt.get_cmap('jet')

        # normally poor quality tracks are filtered out, enabling this will let them through.
        self.disable_track_filters = False
        # disables background subtraction
        self.disable_background_subtraction = False

        os.makedirs(config.tracks_folder, mode=0o775, exist_ok=True)
        self.database = TrackDatabase(os.path.join(self.config.tracks_folder, 'dataset.hdf5'))

        self.worker_pool_init = init_workers

        # load hints.  Hints are a way to give extra information to the tracker when necessary.
        # if os.path.exists(config.tracking.hints_file):
        if config.tracking.hints_file:
            self.load_hints(config.tracking.hints_file)


    def load_hints(self, filename):
        """ Read in hints file from given path.  If file is not found an empty hints dictionary set."""

        self.hints = {}

        if not os.path.exists(filename):
            print("Failed to load hints file '" + filename + "'")
            return

        f = open(filename)
        for line_number, line in enumerate(f):
            line = line.strip()
            # comments
            if line == '' or line[0] == '#':
                continue
            try:
                (filename, file_max_tracks) = line.split()[:2]
            except:
                raise Exception("Error on line {0}: {1}".format(line_number, line))
            self.hints[filename] = int(file_max_tracks)

    def process_all(self, root):

        previous_filter_setting = self.disable_track_filters
        previous_background_setting = self.disable_background_subtraction

        for root, folders, files in os.walk(root):
            for folder in folders:
                if folder not in EXCLUDED_FOLDERS:
                    if folder.lower() == "false-positive":
                        self.disable_track_filters = True
                        self.disable_background_subtraction = True
                        print("Turning Track filters OFF.")
                    self.process_folder(os.path.join(root, folder), tag=folder.lower(), worker_pool_args=(trackdatabase.HDF5_LOCK,))
                    if folder.lower() == "false-positive":
                        print("Restoring Track filters.")
                        self.disable_track_filters = previous_filter_setting
                        self.disable_background_subtraction = previous_background_setting



    def clean_tag(self, tag):
        """
        Removes all clips with given tag.
        :param tag: label to remove
        """
        print("removing tag {}".format(tag))

        ids = self.database.get_all_track_ids()
        for (clip_id, track_number) in ids:
            if not self.database.has_clip(clip_id):
                continue
            meta = self.database.get_track_meta(clip_id, track_number)
            if meta['tag'] == tag:
                print("removing", clip_id)
                self.database.remove_clip(clip_id)


    def clean_all(self):
        """
        Checks if there are any clips in the database that are on the banned list.  Also makes sure no track has more
        tracks than specified in hints file.
        """

        for clip_id, max_tracks in self.hints.items( ):
            if self.database.has_clip(clip_id):
                if max_tracks == 0:
                    print(" - removing banned clip {}".format(clip_id))
                    self.database.remove_clip(clip_id)
                else:
                    meta = self.database.get_clip_meta(clip_id)
                    if meta['tracks'] > max_tracks:
                        print(" - removing out of date clip {}".format(clip_id))
                        self.database.remove_clip(clip_id)


    def process_file(self, full_path, **kwargs):
        """
        Extract tracks from specific file, and assign given tag.
        :param full_path: path: path to CPTV file to be processed
        :param tag: the tag to assign all tracks from this CPTV files
        :param create_preview_file: if enabled creates an MPEG preview file showing the tracking working.  This
            process can be quite time consuming.
        :returns the tracker object
        """

        tag = kwargs['tag']

        base_filename = os.path.splitext(os.path.split(full_path)[1])[0]
        cptv_filename = base_filename + '.cptv'
        preview_filename = base_filename + '-preview' + '.mp4'
        stats_filename = base_filename + '.txt'

        destination_folder = os.path.join(self.config.tracks_folder, tag.lower())

        stats_path_and_filename = os.path.join(destination_folder, stats_filename)

        # read additional information from hints file
        if cptv_filename in self.hints:
            print(cptv_filename)
            print(self.hints[cptv_filename])
            max_tracks = self.hints[cptv_filename]
            if max_tracks == 0:
                return
        else:
            max_tracks = self.config.tracking.max_tracks

        os.makedirs(destination_folder, mode=0o775, exist_ok=True)

        # check if we have already processed this file
        if self.needs_processing(stats_path_and_filename):
            print("Processing {0} [{1}]".format(cptv_filename, tag))
        else:
            return

        # delete any previous files
        tools.purge(destination_folder, base_filename + "*.mp4")

        # load the track
        tracker = TrackExtractor(self.config.tracking)
        tracker.max_tracks = max_tracks
        tracker.tag = tag
        tracker.verbose = self.verbose >= 2

        # by default we don't want to process the moving background images as it's too hard to get good tracks
        # without false-positives.
        tracker.reject_non_static_clips = True

        if self.disable_track_filters:
            tracker.track_min_delta = 0.0
            tracker.track_min_mass = 0.0
            tracker.track_min_offset = 0.0
            tracker.reject_non_static_clips = False

        if self.disable_background_subtraction:
            tracker.disable_background_subtraction = True

        # read metadata
        meta_data_filename = os.path.splitext(full_path)[0] + ".txt"
        if os.path.exists(meta_data_filename):

            meta_data = tools.load_clip_metadata(meta_data_filename)

            tags = set([tag['animal'] for tag in meta_data['Tags'] if 'automatic' not in tag or not tag['automatic']])

            # we can only handle one tagged animal at a time here.
            if len(tags) == 0:
                print(" - Warning, no tags in cptv files, ignoring.")
                return

            if len(tags)>= 2:
                # make sure all tags are the same
                print(" - Warning, mixed tags, can not process.",tags)
                return

            tracker.stats['confidence'] = meta_data['Tags'][0].get('confidence',0.0)
            tracker.stats['trap'] = meta_data['Tags'][0].get('trap','none')
            tracker.stats['event'] = meta_data['Tags'][0].get('event','none')

            # clips tagged with false-positive sometimes come through with a null confidence rating
            # so we set it to 0.8 here.
            if tracker.stats['event'] in ['false-positive', 'false positive'] and tracker.stats['confidence'] is None:
                tracker.stats['confidence'] = 0.8

            tracker.stats['cptv_metadata'] = meta_data
        else:
            self.log_warning(" - Warning: no metadata found for file.")
            return

        start = time.time()

        # save some additional stats
        tracker.stats['version'] = CPTVTrackExtractor.VERSION

        tracker.load(full_path)

        if not tracker.extract_tracks():
            # this happens if the tracker rejected the video for some reason (i.e. too hot, or not static background).
            # we still need to make a record that we looked at it though.
            self.database.create_clip(os.path.basename(full_path), tracker)
            print(" - skipped ({})".format(tracker.reject_reason))
            return tracker

        # assign each track the correct tag
        for track in tracker.tracks:
            track.tag = tag

        if self.enable_track_output:
            tracker.export_tracks(self.database)

        # write a preview
        if self.config.tracking.preview_tracks:
            self.export_mpeg_preview(os.path.join(destination_folder, preview_filename), tracker)

        time_per_frame = (time.time() - start) / len(tracker.frame_buffer)

        # time_stats = tracker.stats['time_per_frame']
        self.log_message(" -tracks: {} {:.1f}sec - Time per frame: {:.1f}ms".format(
             len(tracker.tracks),
             sum(track.duration for track in tracker.tracks),
             time_per_frame * 1000
         ))

        return tracker

    def needs_processing(self, source_filename):
        """
        Returns if given source file needs processing or not
        :param source_filename:
        :return:
        """

        clip_id = os.path.basename(source_filename)

        if self.overwrite_mode == self.OM_ALL:
            return True

        return not self.database.has_clip(clip_id)

    def run_test(self, source_folder, test: TrackerTestCase):
        """ Runs a specific test case. """

        def are_similar(value, expected, relative_error = 0.2, abs_error = 2.0):
            """ Checks of value is similar to expected value. An expected value of 0 will always return true. """
            if expected == 0:
                return True
            return ((abs(value - expected) / expected) <= relative_error) or (abs(value - expected) <= abs_error)

        # find the file.  We looking in all the tag folder to make life simpler when creating the test file.
        source_file = tools.find_file(source_folder, test.source)

        # make sure we don't write to database
        self.enable_track_output = False

        if source_file is None:
            print("Could not find {0} in root folder {1}".format(test.source, source_folder))
            return

        print(source_file)
        tracker = self.process_file(source_file, tag='test')

        # read in stats files and see how we did
        if len(tracker.tracks) != len(test.tracks):
            print("[Fail] {0} Incorrect number of tracks, expected {1} found {2}".format(test.source, len(test.tracks), len(tracker.tracks)))
            return

        for test_result, (expected_duration, expected_movement) in zip(tracker.tracks, test.tracks):

            track_stats = test_result.get_stats()

            if not are_similar(test_result.duration, expected_duration) or not are_similar(track_stats.max_offset, expected_movement):
                print("[Fail] {0} Track too dissimilar expected {1} but found {2}".format(
                    test.source,
                    (expected_duration, expected_movement),
                    (test_result.duration, track_stats.max_offset)))
            else:
                print("[PASS] {0}".format(test.source))

    def export_track_mpeg_previews(self, filename_base, tracker: TrackExtractor):
        """
        Exports preview MPEG for a specific track
        :param filename_base:
        :param tracker:
        :param track:
        :return:
        """

        # resolution of video file.
        # videos look much better scaled up
        FRAME_SIZE = 4*48

        frame_width, frame_height = FRAME_SIZE, FRAME_SIZE
        frame_width =  frame_width // 4 * 4
        frame_height = frame_height // 4 * 4

        for id, track in enumerate(tracker.tracks):
            video_frames = []
            for frame_number in range(len(track.bounds_history)):
                channels = tracker.get_track_channels(track, frame_number)
                img = tools.convert_heat_to_img(channels[1], self.colormap, 0, 350)
                img = img.resize((frame_width, frame_height), Image.NEAREST)
                video_frames.append(np.asarray(img))

            tools.write_mpeg(filename_base+"-"+str(id+1)+".mp4", video_frames)

    def export_mpeg_preview(self, filename, tracker: TrackExtractor):
        """
        Exports tracking information preview to MPEG file.
        """

        self.export_track_mpeg_previews(os.path.splitext(filename)[0], tracker)

        MPEGStreamer = MPEGPreviewStreamer(tracker, self.colormap)

        print("Creating preview MPEG file '" + filename + "'")
        tools.stream_mpeg(filename, MPEGStreamer)

    def run_tests(self, source_folder, tests_file):
        """ Processes file in test file and compares results to expected output. """

        # disable hints for tests
        self.hints = []

        tests = []
        test = None

        # we need to make sure all tests are redone every time.
        self.overwrite_mode = CPTVTrackExtractor.OM_ALL

        # load in the test data
        for line in open(tests_file, 'r'):
            line = line.strip()
            if line == '':
                continue
            if line[0] == '#':
                continue

            if line.split()[0].lower() == 'track':
                if test == None:
                    raise Exception("Can not have track before source file.")
                expected_length, expected_movement = [int(x) for x in line.split()[1:]]
                test.tracks.append((expected_length, expected_movement))
            else:
                test = TrackerTestCase()
                test.source = line
                tests.append(test)

        print("Found {0} test cases".format(len(tests)))

        for test in tests:
            self.run_test(source_folder, test)
