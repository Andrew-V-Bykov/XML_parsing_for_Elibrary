from lxml import etree

path_and_file_name = "conf_unicode.xml"

def validation_xml(file_name: str,
                   ) -> None:
    '''Validate resulted XML. Print "OR" or error!
    
    Parameters:
        file_name (str): Path and File Name for resulted XML.
    
    Return:
        None
    '''
    try:
        etree.parse(file_name)
        print("OK XML")
    except etree.XMLSyntaxError as e:
        print(e)

validation_xml(path_and_file_name)