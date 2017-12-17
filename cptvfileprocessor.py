import numpy as np
import subprocess
from PIL import Image
import os
import glob
import time
import multiprocessing

#####################################
# Helper functions
#####################################

def purge(dir, pattern):
    for f in glob.glob(os.path.join(dir, pattern)):
        os.remove(os.path.join(dir, f))

def find_file(root, filename):
    """
    Finds a file in root folder, or any subfolders.
    :param root: root folder to search file
    :param filename: exact time of file to look for
    :return: returns full path to file or None if not found.
    """
    for root, dir, files in os.walk(root):
        if filename in files:
            return os.path.join(root, filename)
    return None

def process_job(job):
    """ Just a wrapper to pass tupple containing (extractor, *params) to the process_file method. """
    processor = job[0]
    path = job[1]
    params = job[2]
    processor.process_file(path, **params)
    time.sleep(0.001) # apparently gives me a chance to catch the control-c


class CPTVFileProcessor:
    """
    Base class for processing a collection of CPTV video files.
    Supports a worker pool to process multiple files at once.
    """

    # all files will be reprocessed
    OM_ALL = 'all'

    # any clips with a lower version than the current will be reprocessed
    OM_OLD_VERSION = 'old'

    # no clips will be overwritten
    OM_NONE = 'none'

    def __init__(self):
        """
        A base class for processing large sets of CPTV video files.
        """

        # folder to output files to
        self.output_folder = None

        # base folder for source files
        self.source_folder = None

        # number of threads to use when processing jobs.
        self.workers_threads = 0

        # what rule to apply when a destination file already exists.
        self.overwrite_mode = self.OM_NONE

        # default grayscale colormap
        self.colormap = lambda x : (x*255,x*255,x*255)

    def process_file(self, filename):
        """ The function to process an individual file. """
        raise Exception("Process file method must be overwritten in sub class.")

    def needs_processing(self, filename):
        """ Checks if source file needs processing. """
        return True

    def process_folder(self, folder_path, **kargs):
        """Processes all files within a folder."""

        jobs = []

        print('processing',folder_path)

        for file_name in os.listdir(folder_path):
            full_path = os.path.join(folder_path, file_name)
            if os.path.isfile(full_path) and os.path.splitext(full_path )[1].lower() == '.cptv':
                if self.needs_processing(full_path):
                    jobs.append((self, full_path, kargs))

        self.process_job_list(jobs)

    def process_job_list(self, jobs):
        """ Processes a list of jobs. Supports worker threads. """
        if self.workers_threads == 0:
            # just process the jobs in the main thread
            for job in jobs: process_job(job)
        else:
            # send the jobs to a worker pool
            pool = multiprocessing.Pool(self.workers_threads)
            try:
                # see https://stackoverflow.com/questions/11312525/catch-ctrlc-sigint-and-exit-multiprocesses-gracefully-in-python
                pool.map(process_job, jobs, chunksize=1)
                pool.close()
                pool.join()
            except KeyboardInterrupt:
                print("KeyboardInterrupt, terminating.")
                pool.terminate()
                exit()
            else:
                pool.close()

    def log_message(self, message):
        """ Record message in log.  Will be printed if verbose is enabled. """
        # note, python has really good logging... I should probably make use of this.
        if self.verbose: print(message)

    def log_warning(self, message):
        """ Record warning message in log.  Will be printed if verbose is enabled. """
        # note, python has really good logging... I should probably make use of this.
        print("Warning:",message)

if __name__ == '__main__':
    # for some reason the fork method seems to memory leak, and unix defaults to this so we
    # stick to spawn.  Also, form might be a problem as some numpy commands have multiple threads?
    multiprocessing.set_start_method('spawn')
