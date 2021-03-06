'''
A quick sketch to demonstrate uploading library trip material.
'''

import requests
import json
import urllib
import sys, os
import subprocess
from PIL import Image, ImageFilter

import logging
import operator

# Logging copypasta
logger = logging.getLogger('tripperbot')
hdlr = logging.FileHandler('tripperbot.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr) 
logger.setLevel(logging.INFO)


USERNAME = "LibraryTripperBot"
PASSWORD = None

API_URL = "http://wikipaltz.org/api.php"
DIRECTORY = sys.argv[1]


# Settings

SCAN_WIDTH = 100  # How far on either side of the middle we'll scan
DARK_PIXEL_LIMIT = 90 # Set to -1 to disable

COLUMN_TOLERANCE = 380
OUTPUT_SIZE = 1600

VARIANCE_LIMIT = 170 # How much can one pixel differ from the one above it?
ALLOWED_VARIANCES = 3 # How many variances are allowed per column

STREAK_ANNOUNCE = 30  # The number of consecutive hits required to generate a log entry.  No effect on result.

HIT_LIMIT = 8000



def show_userinfo():
    '''
    Prints information about the logged-in user.
    '''
    more_params = dict(action="query", meta="userinfo", format="json")
    r = session.get(API_URL, params=more_params)
    print r.content


def login(username, password, session=requests.Session()):
    '''
    Takes strings username and password
    
    Logs in session with provided credentials.
    
    returns session object
    '''
    login_params = dict(action="login",
        lgname=username,
        lgpassword=password,
        format="json")

    response = session.post(API_URL, params=login_params)
    response_dict = json.loads(response.content)


    print "Getting login token:"
    print response_dict

    login_params['lgtoken'] = response_dict['login']['token']

    second_response = session.post(API_URL, params=login_params)
    response_dict = json.loads(second_response.content)

    print
    print "Attempted to login:"
    print response_dict

    return session


def get_edit_token(page_name, session):
    '''
    In order to edit a page, you need an edit token for your session.
    This takes a session and string page_name and returns such a token.
    '''

    get_edit_token_params = dict(action="query",
                                 format="json",
                                 prop="info",
                                 intoken="edit",
                                 titles=page_name,
                                 )

    if page_name:
            get_edit_token_params['titles'] = page_name

    edit_token_response = session.post(API_URL, params=get_edit_token_params)
    response_dict = json.loads(edit_token_response.content)

    print edit_token_response.content
    edit_token = response_dict['query']['pages']['-1']['edittoken']
    return edit_token


def edit(edit_token):
    '''
    Janky and needs help, but has promise.
    
    Takes an edit_token, edits the LibraryTripperBot's User page on WikiPaltz.
    '''

    edit_params = dict(action="edit",
        title="User:LibraryTripperBot",
        format="json",
        summary="I'm alive!",
        text="Hi.  I'm slashRoot's Library Tripper Bot.  I automate the process of getting content gathered at library trips up on WikiPaltz.",
        token=edit_token)

    print session.headers
    session.headers.update({"Content-Type":"application/x-www-form-urlencoded"})

    edit_response = session.post(API_URL, params=edit_params)
    response_dict = json.loads(edit_response.content)

    print response_dict
    print edit_response.headers


def upload(filename, text, session=requests.Session()):
    '''
    Takes a filename, description text, and Session object.
    Uploads a file to API_URL.
    
    Returns Response object.
    '''
    files = {'file': open(filename, 'rb')}
    token = get_edit_token(filename, session)
    print "Got edit token %s.  Now uploading." % token
    upload_params = dict(action="upload",
                         format="json",
                         ignorewarnings="true",
                         filename=filename,
                         text=text,
                         token=token)

    print upload_params
    upload_response = session.post(API_URL, params=upload_params, files=files)
    return upload_response


def ocr_read(filename, program="tesseract"):
    '''
    Takes a filename of an image.
    
    Reads text in the image using either tesseract or cuneiform.
    '''

    print "Starting %s Read." % program

    if program == "tesseract":
        p = subprocess.Popen('tesseract "%s" output-t' % filename, shell=True, stdout=subprocess.PIPE)

    elif program == "cuneiform":
        p = subprocess.Popen('cuneiform "%s" -o output-c.txt' % filename, shell=True, stdout=subprocess.PIPE)
    else:
        raise ValueError("Don't know how to implement %s - use either tesseract (default) or cuneiform" % program)

    out, err = p.communicate()	
    print out, err

    result = open('output-%s.txt' % program[0], "r").read()
    return result


def resize(filename):
    '''
    Takes string filename, returns Image object
    
    Resizes the file to no larger than OUTPUT_SIZE x OUTPUT_SIZE
    '''
    size = OUTPUT_SIZE, OUTPUT_SIZE
    im = Image.open(filename)
    im.thumbnail(size, Image.ANTIALIAS)
    im.save("%s-resized.jpg" % filename, "JPEG")

    return im


def get_pixel_values(image, left_edge, right_edge, variance_limit=VARIANCE_LIMIT, rlimit=None, llimit=None):
    '''
    Takes an image and starting edges, returns a dict:
       keys are column numbers
       values lists of 2-tuples: (row number, rgb value) 
    '''
    
    
    width = abs(left_edge - right_edge)
    if width < (SCAN_WIDTH / 3):
        logger.warning("Scan too narrow with variance_limit %s.  Raising." % variance_limit)
        left_edge, right_edge = get_starting_edges(image)
        return get_pixel_values(image, left_edge, right_edge, variance_limit=variance_limit + 30)
    
    
    
    logger.info("We'll scan %s columns from %s to %s" % (width, left_edge, right_edge))
    width, height = image.size
    rgb_im = image.convert('RGB')
    
    pixel_dict = {}
    variances = {}
    
    for column in range(left_edge, right_edge): 
        variance_count = 0
        pixel_dict[column] = []
        last_value = None
        
        for row in range(int(height * .2), int(height * .5)):
            r, g, b = rgb_im.getpixel((column, row))
            value = r + g + b
            
            if last_value and abs(value - last_value) > VARIANCE_LIMIT:
                variance_count += 1
                if abs(value - last_value) > VARIANCE_LIMIT * 1.5:
                    logger.warning("Found variance of %s (whoa!) in column %s, row %s" % (abs(value - last_value), column, row) )
            
            if value < DARK_PIXEL_LIMIT: # If we hit a very dark pixel, we'll assume it's text.  Shift to the right or left.
                logger.warning("Very dark pixel (%s) found at column %s, row %s" % (value, column, row))

            if variance_count > ALLOWED_VARIANCES:
                logger.warning("More than %s variances found in column %s.  Shifting to avoid text." % (ALLOWED_VARIANCES, column))
                
            if (value < DARK_PIXEL_LIMIT) or (variance_count > ALLOWED_VARIANCES):
                # First, figure out whether it's the left or right.
                
                nearest = min([left_edge, 
                               right_edge], key=lambda x:abs(x-column))
                
                if nearest == right_edge: # Moving left
                    rlimit = column - 5
                    left_edge = max(left_edge - SCAN_WIDTH / 4, llimit)
                    right_edge -= SCAN_WIDTH / 2
                else: # Moving right
                    llimit = column + 5
                    left_edge += SCAN_WIDTH / 2
                    right_edge = min(right_edge + SCAN_WIDTH / 4,
                                     rlimit or right_edge + SCAN_WIDTH / 4)

                return get_pixel_values(image, left_edge, right_edge, variance_limit=variance_limit, rlimit=rlimit, llimit=llimit)
                
            last_value = value
            
            pixel_dict[column].append((row, value))

    return pixel_dict



def detect_column(pixel_data, tolerance):
    '''
    Takes ints pixel_data and tolerance
    
    returns int of best column
    '''
    
    
    logger.info("Detecting column from data on %s columns at %s tolerance" % (len(pixel_data), tolerance))
    
    hit_pixels = {}
    detected_columns = []
    total_hits = 0
    streaks = {}

    for column, row_info in pixel_data.items():
        last_hit = 0
        last_value = 0
        streaks[column] = 0, 0
        
        streak = 0
        for row, value in row_info:
            if value < tolerance:
                # hit!
                total_hits += 1
                streak += 1
                if streak > streaks[column][0]:
                    streaks[column] = streak, row
                last_hit = row
            else:
                # No hit.
                if streak > STREAK_ANNOUNCE:
                    logger.info("Breaking streak of %s on column %s at row %s." % (streak, column, row))
                streak = 0
        
    best_column, (longest_streak, ending_row) = max(streaks.iteritems(), key=operator.itemgetter(1))
    
    logger.info("Longest streak: %s in column %s ending at row %s" % (longest_streak, best_column, ending_row))
    
    if total_hits > HIT_LIMIT:
        logger.warning("Too many hits (%s) at tolerance %s." % (total_hits, tolerance))
        return detect_column(pixel_data, tolerance - 20)
    
    logger.info("Total hits: %s" % total_hits)
    return best_column



def get_starting_edges(image):
    '''
    Takes image, uses SCAN_WIDTH to figure out where to start and end.
    Return tuple of ints left and right
    '''
    width, height = image.size

    # We only want to scan the middle of the image.
    left = (width / 2) - SCAN_WIDTH
    right = (width / 2) + SCAN_WIDTH
    
    return left, right


def find_column_from_image(filename=None, image=None, tolerance=COLUMN_TOLERANCE):
    '''
    Higher level function.
    
    Takes either image, an Image object or filename, a str
    Open files, bats it around with the above functions
    to figure out where its column is.
    
    Returns int result (the pixel number of the column) 
    '''
    if not image:
        image = Image.open(filename)


    left_start, right_end = get_starting_edges(image)


    pixel_dict = get_pixel_values(image, left_start, right_end)
    result = detect_column(pixel_dict, tolerance)

    return result


def split_vertical(filename):
    '''
    Takes string filename
    Splits into two images by finding column
    
    Saves them as filename-left.jpg and filename-right.jpg
    
    returns left and right Image objects
    '''
    image = Image.open(filename)
    width, height = image.size    

    column = find_column_from_image(image=image)
    logger.info("Found column at %s" % (column))
    left_crop = image.crop((0, 0, column, height))
    right_crop = image.crop((column, 0, width, height))

    left_crop.save('output/%s-left.jpg' % filename.split('/')[-1])
    right_crop.save('output/%s-right.jpg' % filename.split('/')[-1])
    
    return left_crop, right_crop



# Main


session = login(USERNAME, PASSWORD)

for filename in os.listdir(DIRECTORY):

    # NOT IMPLEMENTED
    #if "==BLAHBLAH==" in filename:
    #    left, right = split_vertical(filename)




    # First let's split this file vertically

    full_path = DIRECTORY + filename

    if "==NOCR==" in filename:
        file_text = "[[Category:No OCR]][[Category:Uncurated Images]]"
    else:
        file_text = "==Tesseract OCR Result==\n%s\
    		  \n==Cuneiform OCR Result==\n%s\
    		  \n[[Category:Uncurated Images]][[Category:OCR]]" % (ocr_read(full_path), ocr_read(full_path, program="cuneiform"))

    if "==PLU==" in filename:
        file_text += "[[Category:Human Attention Needed]]"
    
    if '==SJ==' in filename:
        file_text += "[[Category:Social Justice]]"
    
    if "==SH==" in filename:
        file_text += "[[Category:Student Housing]]"


    resize(DIRECTORY + filename)
    upload("%s-resized.jpg" % full_path, file_text, session=session).content


